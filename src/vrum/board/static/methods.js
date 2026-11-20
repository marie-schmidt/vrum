// How the signed-in person can sign in: the Sign-in methods setting of the Users page. It is drawn only
// when the server offers another way to sign in (or when the account has no password), and lists the
// password and each provider the server is set up for. The page keeps no rule about which way may be
// taken away: the server refuses to remove the last one, in its own words, and the dialog shows them.
//
// OpenID and GitHub are connected by going to the provider and coming back (sso.js); a directory is
// connected by typing its username and password in a dialog, which Vrum checks against the directory
// and does not keep.

import { Refusal, Stale, identities, linkLdap, providersOn, setPassword, unlinkIdentity } from './api.js';
import { account, notify } from './ctx.js';
import { h, moment, put } from './dom.js';
import { dialogForm, field, openDialog, refuse } from './forms.js';
import { icon } from './icons.js';
import { DIRECTORY_WORDS, goToProvider, ssoIcon } from './sso.js';

let generation = 0;
let holder = null; // the element that holds the methods, while the Users page is open
let known = null; // what the server said: { has_password, identities }
let changePassword = null; // opens the dialog that changes an existing password
const problem = () => holder?.querySelector('#methods-problem');

// Whether the Users page draws this at all: not when the server offers only the password and the
// account has one, which is the page as it has always been.
export const showsMethods = () => providersOn().length > 0 || account.has_password === false;

// Stop answering for the page that is gone.
export function forgetMethods() {
  generation += 1;
  holder = null;
  known = null;
}

// "a", "a or b", "a, b or c".
const orList = (names) => (names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} or ${names[names.length - 1]}`);

// One way to sign in: what it is, where it stands, and what can be done about it.
function method(id, iconName, title, state, quiet, ...actions) {
  return h(
    'li',
    { class: 'method', id: `method-${id}`, dataset: { method: id } },
    h('span', { class: 'method-icon', 'aria-hidden': 'true' }, icon(iconName, { size: 18 })),
    h('div', { class: 'method-main' }, h('p', { class: 'method-title' }, title), h('p', { class: 'method-state', id: `method-${id}-state` }, state), quiet ? h('p', { class: 'method-quiet' }, quiet) : null),
    h('div', { class: 'method-actions' }, actions),
  );
}

function passwordMethod() {
  if (known.has_password) {
    return method('password', 'lock', 'Password', 'You can sign in with your email and password', null, h('button', { type: 'button', id: 'change-password', onclick: () => changePassword() }, 'Change password…'));
  }
  const ways = known.identities.map((identity) => identity.name);
  const state = `You have no password. ${ways.length ? `Sign in with ${orList(ways)}. ` : ''}Set a password to also sign in with email.`;
  return method('password', 'lock', 'Password', state, null, h('button', { type: 'button', id: 'set-password', onclick: setPasswordDialog }, 'Set a password…'));
}

function providerMethod(provider) {
  const found = known.identities.find((identity) => identity.provider === provider.id);
  const iconName = ssoIcon(provider.id);
  if (!found) {
    const connect = h('button', { type: 'button', id: `connect-${provider.id}`, onclick: () => (provider.id === 'ldap' ? directoryDialog(provider) : goToProvider(provider, 'link', connect, problem())) }, `Connect ${provider.name}${provider.id === 'ldap' ? '…' : ''}`);
    return method(provider.id, iconName, provider.name, 'Not connected', null, connect);
  }
  const who = found.display_name || found.email;
  const extra = [found.email && found.email !== found.display_name ? found.email : null, found.linked_at ? `Connected ${moment(found.linked_at)}` : null].filter(Boolean).join(' · ');
  return method(
    provider.id,
    iconName,
    provider.name,
    who ? `Connected as ${who}` : 'Connected',
    extra,
    h('button', { type: 'button', id: `disconnect-${provider.id}`, onclick: () => disconnectDialog(provider) }, 'Disconnect…'),
  );
}

function draw() {
  if (!holder || !known) return;
  put(holder, h('ul', { class: 'methods plain', id: 'methods' }, passwordMethod(), providersOn().map(providerMethod)), h('p', { class: 'problem', role: 'alert', id: 'methods-problem' }));
}

async function load(mine) {
  if (providersOn().length === 0) {
    known = { has_password: account.has_password !== false, identities: [] };
    draw();
    return;
  }
  try {
    const found = await identities();
    if (mine !== generation || !holder) return;
    known = found;
    account.has_password = found.has_password;
    draw();
  } catch (error) {
    if (mine !== generation || !holder || error instanceof Stale) return;
    if (!(error instanceof Refusal)) throw error;
    const failed = h('p', { class: 'problem', role: 'alert', id: 'methods-problem' });
    refuse(failed, error.message);
    put(holder, failed, h('button', { type: 'button', id: 'methods-retry', onclick: () => load(generation) }, 'Try again'));
  }
}

// The setting's control: the list of ways, once the server has said which are connected.
// `openChangePassword` opens the page's dialog for changing a password that exists.
export function methodsControl(openChangePassword) {
  generation += 1;
  changePassword = openChangePassword;
  known = null;
  holder = h('div', { class: 'methods-control', id: 'methods-control' }, h('p', { class: 'loading', role: 'status' }, 'Loading how you sign in…'));
  load(generation);
  return holder;
}

// -- the dialogs ---------------------------------------------------------------------------------------

function setPasswordDialog() {
  const next = h('input', { type: 'password', id: 'password-new', name: 'new', autocomplete: 'new-password' });
  const again = h('input', { type: 'password', id: 'password-again', name: 'again', autocomplete: 'new-password' });
  const { form } = dialogForm({
    fields: [
      h('p', {}, `You will be able to sign in with ${account.email} and this password, as well as the ways you have now.`),
      field('New password', next, 'At least 10 characters.'),
      field('The new password again', again),
    ],
    submit: 'Set password',
    work: async () => {
      if (next.value !== again.value) throw new Refusal('The two passwords are not the same. Nothing was changed.');
      await setPassword(next.value);
      account.has_password = true;
      if (known) known.has_password = true;
      form.closest('dialog')?.close();
      notify('Your password is set. You can now sign in with your email and password too.');
      draw();
    },
  });
  openDialog('Set a password', [form]);
  next.focus();
}

function disconnectDialog(provider) {
  const { form } = dialogForm({
    fields: [h('p', {}, `You will no longer be able to sign in with ${provider.name}. Your Vrum account stays as it is, with everything in it, and you can connect ${provider.name} again later.`)],
    submit: `Disconnect ${provider.name}`,
    work: async () => {
      await unlinkIdentity(provider.id);
      form.closest('dialog')?.close();
      notify(`${provider.name} is disconnected. Your other sessions have ended.`);
      await load(generation);
    },
  });
  openDialog(`Disconnect ${provider.name}`, [form]);
}

function directoryDialog(provider) {
  const username = h('input', { type: 'text', id: 'link-ldap-username', name: 'username', autocomplete: 'off', autocapitalize: 'none', spellcheck: 'false' });
  const password = h('input', { type: 'password', id: 'link-ldap-password', name: 'password', autocomplete: 'off' });
  const { form } = dialogForm({
    fields: [field('Username', username), field('Password', password, DIRECTORY_WORDS)],
    submit: `Connect ${provider.name}`,
    work: async () => {
      await linkLdap(username.value, password.value);
      form.closest('dialog')?.close();
      notify(`${provider.name} is now connected to your account.`);
      await load(generation);
    },
  });
  openDialog(`Connect ${provider.name}`, [form]);
  username.focus();
}
