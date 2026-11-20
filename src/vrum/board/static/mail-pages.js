// The two pages the links in Vrum's emails open: choosing a new password (/board/reset/<token>) and
// confirming an email address (/board/verify/<token>). Both work signed in or signed out, and both
// say in plain words what happened.
//
// The token in the address is read once and taken out of the address bar at once, so it is not left
// in the browser's history or a bookmark; from then on it lives in this page's memory only and is
// kept nowhere. Opening a page never uses a link (a mail scanner may open it): the page asks the
// server to, and the server answers with a refusal in its own words for a link that was used, has
// run out or never was.

import { Refusal, Unreachable, checkPasswordReset, currentAccount, mailOn, resetPassword, signOut, verifyEmail } from './api.js';
import { h, put } from './dom.js';
import { heading, labelled, sendWith, showForgot } from './entry.js';
import { refuse } from './forms.js';
import { icon } from './icons.js';

const forgetAddress = () => history.replaceState(null, '', '/board');
const card = (id, tone, ...children) => h('div', { class: 'entry-wrap' }, h('section', { class: `entry mail-page ${tone}`, id }, ...children));
const loading = (main, text) => put(main, h('p', { class: 'loading', role: 'status' }, text));

// The way on from a page that is finished: to the person's projects, or to sign in.
const goOn = (signedIn) => h('a', { href: '/board', class: 'link-button primary mail-continue', id: 'mail-continue' }, signedIn ? 'Go to your projects' : 'Sign in');

// A link that cannot be used: the server's words, and what to do next.
function cannotUse(main, { id, title, error, retry, next }) {
  const problem = h('p', { class: 'problem', role: 'alert', id: `${id}-problem` });
  refuse(problem, error.message);
  put(
    main,
    card(
      id,
      'bad',
      heading('alert', title),
      problem,
      error instanceof Unreachable ? h('button', { type: 'button', class: 'primary mail-continue', id: 'mail-retry', onclick: retry }, 'Try again') : next,
    ),
  );
}

// -- choosing a new password -----------------------------------------------------------------------------

export async function showReset(main, token) {
  document.title = 'Choose a new password · Vrum';
  forgetAddress();
  loading(main, 'Checking the link…');
  try {
    await checkPasswordReset(token);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    cannotUse(main, {
      id: 'reset-link',
      title: 'This link cannot be used',
      error,
      retry: () => showReset(main, token),
      next: h(
        'div',
        { class: 'mail-ways' },
        mailOn() ? h('button', { type: 'button', class: 'primary', id: 'reset-ask-again', onclick: () => showForgot(main, { back: () => location.assign('/board') }) }, icon('mail', { size: 16 }), 'Ask for a new link') : null,
        h('a', { href: '/board', class: 'link-button', id: 'back-to-sign-in' }, 'Back to sign in'),
      ),
    });
    return;
  }
  drawReset(main, token);
}

function drawReset(main, token) {
  const next = h('input', { type: 'password', id: 'reset-new', name: 'new', autocomplete: 'new-password' });
  const again = h('input', { type: 'password', id: 'reset-again', name: 'again', autocomplete: 'new-password' });
  const problem = h('p', { class: 'problem', role: 'alert', id: 'reset-new-problem' });
  const submit = h('button', { type: 'submit', class: 'primary', id: 'reset-save' }, 'Save password');
  const form = h(
    'form',
    {
      id: 'reset-password-form',
      novalidate: true,
      onsubmit: sendWith(problem, submit, async () => {
        if (next.value !== again.value) throw new Refusal('The two passwords are not the same. Nothing was changed.');
        await resetPassword(token, next.value);
        await leaveOtherSession();
        drawDone(main);
      }),
    },
    labelled('reset-new', 'New password', next, 'At least 10 characters.'),
    labelled('reset-again', 'The new password again', again),
    problem,
    submit,
  );
  put(main, card('reset-page', '', heading('lock', 'Choose a new password'), form));
  next.focus();
}

// The browser may still be signed in as somebody else; the new password is for signing in, so it
// starts from a signed-out page. (The reset ended the account's own sessions.)
async function leaveOtherSession() {
  try {
    if (await currentAccount()) await signOut();
  } catch {
    // Nothing to leave, or the server could not be asked: the page goes on either way.
  }
}

function drawDone(main) {
  const done = h('div', { class: 'mail-done', id: 'reset-done', role: 'status', tabindex: '-1' }, h('p', {}, 'Your password is changed. Sign in with the new one.'));
  put(main, card('reset-page', 'ok', heading('check', 'Password changed'), done, goOn(false)));
  done.focus();
}

// -- confirming an email address -------------------------------------------------------------------------

export async function showVerify(main, token, { signedIn }) {
  document.title = 'Confirm your email address · Vrum';
  forgetAddress();
  loading(main, 'Confirming your email address…');
  let confirmed;
  try {
    confirmed = await verifyEmail(token);
  } catch (error) {
    if (!(error instanceof Refusal)) throw error;
    cannotUse(main, { id: 'verify-link', title: 'This link cannot be used', error, retry: () => showVerify(main, token, { signedIn }), next: goOn(signedIn) });
    return;
  }
  put(
    main,
    card(
      'verify-page',
      'ok',
      heading('check', 'Email address confirmed'),
      h('p', { id: 'verify-done', role: 'status' }, confirmed.email ? `${confirmed.email} is confirmed on your Vrum account.` : 'Your email address is confirmed.'),
      goOn(signedIn),
    ),
  );
}
