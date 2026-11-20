// Signing in with another provider: OpenID Connect, GitHub, or a directory over LDAP. All of it
// exists only when the server says it is set up (`providers` in the session answer): with none, the
// board draws nothing about it. Nothing here decides who may sign in or checks a password; the page
// sends the person to the provider (OpenID and GitHub) or sends what was typed to the server (a
// directory), and shows the server's answer in its own words. Whatever a provider says about a person
// is only ever put on a page as text.
//
// OpenID and GitHub leave the page and come back to it. The way back is the same address with
// `?sso_error=<code>&sso_provider=<id>` (a problem) or `?sso=linked` (a provider was connected), which
// takeSsoResult reads once and takes out of the address, so a reload or a copied link does not say it again.

import { Refusal, createAccountsOn, ldapSignIn, providersOn, startProvider } from './api.js';
import { h } from './dom.js';
import { field, refuse } from './forms.js';
import { icon } from './icons.js';

// What a provider is called when the server no longer lists it (it was taken away while a sign-in
// was on its way).
const DEFAULT_NAMES = { oidc: 'OpenID', github: 'GitHub', ldap: 'LDAP' };
// A shield for an identity service, a branch for a code host and a tree for a directory: plain shapes,
// not anyone's logo.
const ICONS = { oidc: 'shield', github: 'branch', ldap: 'hier' };

export const ssoIcon = (id) => (Object.hasOwn(ICONS, id) ? ICONS[id] : 'key');

export function providerName(id) {
  const known = providersOn().find((provider) => provider.id === id);
  if (known) return known.name;
  return Object.hasOwn(DEFAULT_NAMES, id) ? DEFAULT_NAMES[id] : 'that provider';
}

// Every problem a sign-in or a connection can come back with, in the words the server uses for the
// same thing. `name` is what the person knows the provider as.
const PROBLEMS = {
  cancelled: (name) => `Signing in with ${name} was cancelled. Nothing was changed.`,
  failed: (name) => `Signing in with ${name} did not work. Nothing was changed. Try again, and if it keeps happening ask whoever runs this server.`,
  expired: () => 'That sign-in took too long or was already used. Start again.',
  unavailable: (name) => `Vrum could not reach ${name}. Try again in a moment.`,
  no_verified_email: (name) => `${name} did not give a confirmed email address, so Vrum cannot tell who you are. Confirm your email address there, or sign in another way.`,
  domain_not_allowed: () => 'Accounts with that email address may not sign in this way on this server.',
  account_exists_unconfirmed: (name) => `An account with your email address already exists here, but its address was never confirmed, so Vrum will not join the two. Sign in with its password, then connect ${name} from the Users page.`,
  signups_off: (name) => `This server does not make new accounts from ${name} sign-in. Sign in with an account you already have, or ask whoever runs it.`,
  identity_taken: (name) => `That ${name} account is already connected to another Vrum account.`,
  already_connected: (name) => `${name} is already connected to this account. Disconnect it first.`,
  link_session_lost: (name) => `You were signed out while connecting ${name}. Sign in and try again.`,
  last_way_in: () => 'That is the only way you can sign in. Set a password or connect another provider first. Nothing was changed.',
};

// The plain words for a problem code. A code this page does not know says the general thing, so
// nothing the address holds is ever shown as it is.
export function problemWords(code, provider) {
  const words = Object.hasOwn(PROBLEMS, code) ? PROBLEMS[code] : PROBLEMS.failed;
  return words(providerName(provider));
}

// What the address says about a sign-in that has just come back: { error, provider } for a problem,
// { linked: true, provider } for a connection that was made, or null. It is taken out of the address
// at once.
export function takeSsoResult() {
  const params = new URLSearchParams(location.search);
  const named = ['sso', 'sso_error', 'sso_provider'].filter((key) => params.has(key));
  if (named.length === 0) return null;
  const found = { error: params.get('sso_error'), linked: params.get('sso') === 'linked', provider: params.get('sso_provider') };
  for (const key of named) params.delete(key);
  const rest = params.toString();
  history.replaceState(history.state, '', `${location.pathname}${rest ? `?${rest}` : ''}${location.hash}`);
  if (found.error !== null) return { error: found.error, provider: found.provider };
  return found.linked ? { linked: true, provider: found.provider } : null;
}

// -- going to a provider and coming back ----------------------------------------------------------------

// Where the provider sends the browser back to: the project's board, or the invitation being answered,
// with the page the person was on (the server keeps a path of the board's own and sends anything else
// to the board).
const returnPath = () => {
  if (!/^\/board\/(?:p\/\d+|invite\/[^/]+)\/?$/.test(location.pathname)) return undefined;
  const back = `${location.pathname}${location.hash}`;
  return back.length <= 400 ? back : location.pathname;
};

// A button that has sent the page away stays off. If the person comes back with the Back button the
// browser may show the page as it was left, so the buttons are put right again.
const held = new Set();
const hold = (button) => {
  held.add(button);
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
};
const release = (button) => {
  held.delete(button);
  button.disabled = false;
  button.removeAttribute('aria-busy');
};
addEventListener('pageshow', (event) => {
  if (event.persisted) [...held].forEach(release);
});

// Only a web address is a place to send the browser to, whatever the server says.
function destination(address, provider) {
  const target = new URL(address, location.href);
  if (target.protocol !== 'https:' && target.protocol !== 'http:') throw new Refusal(problemWords('failed', provider));
  return target.href;
}

// Ask the server where to go, and go. `intent` is signin, or link for a signed-in person connecting
// the provider to their account. A refusal (nothing set up, too many tries) is said in `problem`.
export async function goToProvider(provider, intent, button, problem) {
  problem.replaceChildren();
  hold(button);
  try {
    const answer = await startProvider(provider.id, intent, returnPath());
    location.assign(destination(answer.url, provider.id));
  } catch (error) {
    release(button);
    if (!(error instanceof Refusal)) throw error;
    refuse(problem, error.message);
  }
}

// -- under the sign-in and create-account forms --------------------------------------------------------

export const DIRECTORY_WORDS = 'Your password goes to Vrum, which checks it with the directory. Vrum does not keep it.';

const providerButton = (provider, label, props = {}) =>
  h('button', { type: 'button', class: 'sso-button', id: `sso-${provider.id}`, dataset: { provider: provider.id }, ...props }, icon(ssoIcon(provider.id), { size: 18 }), h('span', {}, label));

// A directory has no page to go to: its name and password are asked for here, in place, and the
// server checks them against it.
function directoryWay(provider, verb, onSignedIn) {
  const username = h('input', { type: 'text', id: 'ldap-username', name: 'username', autocomplete: 'username', autocapitalize: 'none', spellcheck: 'false' });
  const password = h('input', { type: 'password', id: 'ldap-password', name: 'password', autocomplete: 'current-password' });
  const problem = h('p', { class: 'problem', role: 'alert', id: 'ldap-problem' });
  const submit = h('button', { type: 'submit', class: 'primary', id: 'ldap-submit' }, verb === 'Continue with' ? 'Continue' : 'Sign in');
  const toggle = providerButton(provider, `${verb} ${provider.name}`, { 'aria-expanded': 'false', 'aria-controls': 'ldap-form' });
  const show = (open) => {
    form.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    if (open) username.focus();
  };
  const form = h(
    'form',
    {
      id: 'ldap-form',
      class: 'sso-form',
      novalidate: true,
      hidden: true,
      'aria-labelledby': toggle.id,
      onsubmit: async (event) => {
        event.preventDefault();
        problem.replaceChildren();
        submit.disabled = true;
        try {
          onSignedIn(await ldapSignIn(username.value, password.value));
        } catch (error) {
          if (!(error instanceof Refusal)) throw error;
          refuse(problem, error.message);
          // The button that was pressed was off for a moment, which takes the focus with it: put it where the
          // person goes next.
          password.focus();
          password.select();
        } finally {
          submit.disabled = false;
        }
      },
    },
    field('Username', username),
    field('Password', password, DIRECTORY_WORDS),
    problem,
    submit,
  );
  toggle.addEventListener('click', () => show(form.hidden));
  const way = h('div', { class: 'sso-way' }, toggle, form);
  // Escape puts the form away, from the form or from its button.
  way.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || form.hidden) return;
    show(false);
    toggle.focus();
  });
  return way;
}

// A quiet "or" and one button for each provider the server is set up for, to go under a form.
// `creating` is the create-account page. Without providers there is nothing, and with the server
// not making accounts from a provider's sign-in there is nothing on the create-account page, whose
// form is for a password account.
export function providerWays({ creating, onSignedIn }) {
  const found = providersOn();
  if (found.length === 0 || (creating && !createAccountsOn())) return null;
  const verb = creating ? 'Continue with' : 'Sign in with';
  const problem = h('p', { class: 'problem', role: 'alert', id: 'sso-problem' });
  const ways = found.map((provider) => {
    if (provider.id === 'ldap') return directoryWay(provider, verb, onSignedIn);
    const button = providerButton(provider, `${verb} ${provider.name}`, { onclick: () => goToProvider(provider, 'signin', button, problem) });
    return button;
  });
  return h(
    'div',
    { class: 'sso', id: 'sso-ways', role: 'group', 'aria-label': creating ? 'Other ways to create an account' : 'Other ways to sign in' },
    h('p', { class: 'sso-divider', 'aria-hidden': 'true' }, h('span', {}, 'or')),
    ways,
    problem,
  );
}
