// Account (#/account): the signed-in person and nothing about the project. It is reached from the account
// menu at the foot of the sidebar, not from the Settings menu, because it follows the person into every
// project.
//
//   Name      the name the person is shown as in every history. The server stamps it on what they do: no
//             page lets anyone type a name for the history.
//   Email     the address, which cannot be changed yet, and, when the server can send email, whether it is
//             confirmed, with a button that emails a link to confirm it.
//   Password  one setting with a button to change it. When the server offers other ways to sign in (or the
//             account has no password) it is the list of sign-in methods instead: the password and each
//             provider, connected or not (methods.js).
//   Email nudges  the last setting: for each project the person is in, the kinds of news they ask to be emailed about (nudges.js).

import { Refusal, changePassword, currentAccount, mailOn, renameAccount, sendVerification } from './api.js';
import { account, notify } from './ctx.js';
import { h, put } from './dom.js';
import { dialogForm, field, openDialog, refuse } from './forms.js';
import { icon } from './icons.js';
import { forgetMethods, methodsControl, showsMethods } from './methods.js';
import { forgetNudges, nudgesSection } from './nudges.js';
import { pageHeader } from './page.js';
import { updateAccount } from './sidebar.js';

let root = null;
let sentTo = ''; // the address a verification email was just sent to, said under the Email setting
let generation = 0;

// From the Email nudges section to where an address is confirmed.
function toEmail() {
  const button = document.getElementById('send-verification');
  if (button) button.focus();
  else document.getElementById('setting-email')?.scrollIntoView({ block: 'center' });
}

export function mount(element) {
  root = element;
  sentTo = '';
  generation += 1;
  put(
    root,
    h(
      'div',
      { class: 'settings-page account-page' },
      pageHeader('Account'),
      h('section', { id: 'account-settings', class: 'settings-section', 'aria-label': 'Your account' }, h('div', { class: 'settings-list' }, nameSetting(), emailSetting(), passwordSetting(), nudgesSection(toEmail))),
    ),
  );
  // The address may have been confirmed in another tab or on a phone: look again when the person comes back to this one.
  document.addEventListener('visibilitychange', comeBack);
  rereadAccount();
}

export function unmount() {
  generation += 1;
  forgetMethods();
  forgetNudges();
  document.removeEventListener('visibilitychange', comeBack);
  root = null;
}

export function update() {}

export function refresh() {}

// One line of settings: what it is on the left, the thing to do on the right.
function setting(id, title, ...control) {
  return h('div', { class: 'setting', id }, h('div', { class: 'setting-text' }, title), h('div', { class: 'setting-control' }, ...control));
}

const settingTitle = (text) => h('p', { class: 'setting-title' }, text);

// -- name -----------------------------------------------------------------------------------------------

function nameSetting() {
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
  return setting(
    'setting-name',
    h('label', { for: 'account-display-name', class: 'setting-title' }, 'Name'),
    h('form', { id: 'name-form', class: 'setting-form', novalidate: true, onsubmit: rename }, name, save),
    problem,
  );
}

// -- email ----------------------------------------------------------------------------------------------

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
    // What the person may ask for depends on the address being confirmed.
    root.querySelector('#account-nudges')?.replaceWith(nudgesSection(toEmail));
  } catch {
    // Not being able to ask changes nothing on the page: it is asked again when the person comes back.
  }
}

const comeBack = () => {
  if (document.visibilityState === 'visible') rereadAccount();
};

// -- password and the other ways in ---------------------------------------------------------------------------

// With another way to sign in (or an account that has no password) the password is one of the sign-in
// methods; otherwise it is the one setting it has always been.
function passwordSetting() {
  return showsMethods()
    ? setting('setting-methods', settingTitle('Sign-in methods'), methodsControl(passwordDialog))
    : setting('setting-password', settingTitle('Password'), h('button', { type: 'button', id: 'change-password', onclick: passwordDialog }, icon('lock', { size: 16 }), 'Change password…'));
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
