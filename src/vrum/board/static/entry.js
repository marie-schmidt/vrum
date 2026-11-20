// What the board shows before a project is open: signing in, creating an account, and the person's
// own projects. Nothing here is checked in the browser: each form sends what was typed and shows
// the server's refusal in its own words, so the page and an API client are told the same thing.
// It is a quiet, centred card on the page, with no sidebar; the appearance control is in the top bar.

import { Refusal, createProject, mailOn, projects, register, requestPasswordReset, signIn } from './api.js';
import { h, moment, put } from './dom.js';
import { refuse } from './forms.js';
import { icon } from './icons.js';
import { roleChip } from './labels.js';
import { providerWays } from './sso.js';

const projectAddress = (project) => `/board/p/${project.id}#/board`;
const initial = (name) => ([...String(name).trim()][0] || '?').toUpperCase();

export function labelled(id, label, control, help = null) {
  const note = help ? h('p', { class: 'hint', id: `${id}-help` }, help) : null;
  if (note) control.setAttribute('aria-describedby', note.id);
  return h('div', { class: 'field' }, h('label', { for: id }, label), control, note);
}

// Send a form's work, and show a refusal under it. The buttons stay off while it goes.
export function sendWith(problem, button, work) {
  return async (event) => {
    event.preventDefault();
    problem.replaceChildren();
    button.disabled = true;
    try {
      await work();
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      refuse(problem, error.message);
    } finally {
      button.disabled = false;
    }
  };
}

// The head of a card: what it is, in a heading.
export const heading = (iconName, title) =>
  h('header', { class: 'entry-head' }, h('span', { class: 'entry-icon', 'aria-hidden': 'true' }, icon(iconName, { size: 20 })), h('h1', {}, title));

// The page that asks for an email with a link to choose a new password. It is only offered when the
// server can send email. Whether or not the address has an account, the answer is the same words, so
// the page cannot be used to find out who has one. `back` goes back to the sign-in page.
export function showForgot(main, { email = '', back }) {
  document.title = 'Reset your password · Vrum';
  const address = h('input', { type: 'text', inputmode: 'email', id: 'reset-email', name: 'email', autocomplete: 'email', spellcheck: 'false', value: email });
  const problem = h('p', { class: 'problem', role: 'alert', id: 'reset-problem' });
  const submit = h('button', { type: 'submit', class: 'primary', id: 'reset-submit' }, icon('mail', { size: 16 }), 'Send the link');
  const sent = h('div', { class: 'mail-sent', id: 'reset-sent', role: 'status', tabindex: '-1', hidden: true });
  const form = h(
    'form',
    {
      id: 'reset-form',
      novalidate: true,
      onsubmit: sendWith(problem, submit, async () => {
        const answer = await requestPasswordReset(address.value);
        form.hidden = true;
        put(sent, icon('check', { size: 18, className: 'mail-sent-icon' }), h('p', { id: 'reset-sent-text' }, answer.message));
        sent.hidden = false;
        sent.focus();
      }),
    },
    labelled('reset-email', 'Email', address),
    problem,
    submit,
  );
  put(
    main,
    h(
      'div',
      { class: 'entry-wrap' },
      h(
        'section',
        { class: 'entry', id: 'reset-request' },
        heading('mail', 'Reset your password'),
        form,
        sent,
        h('p', { class: 'entry-switch' }, h('button', { type: 'button', class: 'link', id: 'back-to-sign-in', onclick: back }, 'Back to sign in')),
      ),
    ),
  );
  address.focus();
}

// The sign-in and registration page. `onSignedIn(session)` is called once the server has signed
// the person in, whichever of the two they used. `intro` is the line above the card.
export function showSignIn(main, { onSignedIn, message = '', notice = '', registering = false, intro = 'One shared place for the plan and the notes behind it, for people and AI agents.' }) {
  const draw = (creating) => {
    document.title = 'Vrum board';
    const email = h('input', { type: 'text', inputmode: 'email', id: 'account-email', name: 'email', autocomplete: creating ? 'email' : 'username', spellcheck: 'false' });
    const password = h('input', { type: 'password', id: 'account-password', name: 'password', autocomplete: creating ? 'new-password' : 'current-password' });
    const name = creating ? h('input', { type: 'text', id: 'account-name', name: 'name', autocomplete: 'name' }) : null;
    const problem = h('p', { class: 'problem', role: 'alert', id: 'entry-problem' });
    if (message) refuse(problem, message);
    // What went wrong with a sign-in by a provider is about no field of the form, so it is said above it.
    const result = notice ? h('p', { class: 'problem sso-notice', role: 'alert', id: 'sso-notice' }) : null;
    if (result) refuse(result, notice);
    const submit = h('button', { type: 'submit', class: 'primary', id: creating ? 'register-submit' : 'sign-in-submit' }, creating ? 'Create account' : 'Sign in');
    // Choosing a new password by email exists only when the server can send email.
    const forgot =
      !creating && mailOn()
        ? h('p', { class: 'entry-forgot' }, h('button', { type: 'button', class: 'link', id: 'forgot-password', onclick: () => showForgot(main, { email: email.value, back: () => draw(false) }) }, 'Forgot your password?'))
        : null;
    const form = h(
      'form',
      {
        id: creating ? 'register-form' : 'sign-in-form',
        novalidate: true,
        onsubmit: sendWith(problem, submit, async () =>
          onSignedIn(creating ? await register(email.value, name.value, password.value) : await signIn(email.value, password.value)),
        ),
      },
      name ? labelled('account-name', 'Your name', name) : null,
      labelled('account-email', 'Email', email),
      labelled('account-password', 'Password', password, creating ? 'At least 10 characters.' : null),
      forgot,
      problem,
      submit,
    );
    put(
      main,
      h(
        'div',
        { class: 'entry-wrap' },
        h('p', { class: 'entry-intro', id: 'entry-intro' }, intro),
        h(
          'section',
          { class: 'entry' },
          heading(creating ? 'person' : 'lock', creating ? 'Create an account' : 'Sign in to Vrum'),
          result,
          form,
          providerWays({ creating, onSignedIn }),
          creating
            ? h('p', { class: 'entry-switch' }, 'Have an account already? ', h('button', { type: 'button', class: 'link', id: 'show-sign-in', onclick: () => draw(false) }, 'Sign in'))
            : h('p', { class: 'entry-switch' }, 'New here? ', h('button', { type: 'button', class: 'link', id: 'show-register', onclick: () => draw(true) }, 'Create an account')),
        ),
      ),
    );
    (creating ? name : email).focus();
  };
  draw(registering);
}

// The person's own projects, with a real empty state and a way to make one.
export async function showProjects(main, session) {
  const found = await projects();
  const name = h('input', { type: 'text', id: 'project-name', name: 'name', autocomplete: 'off' });
  const problem = h('p', { class: 'problem', role: 'alert', id: 'project-problem' });
  const submit = h('button', { type: 'submit', class: 'primary', id: 'create-project' }, icon('plus', { size: 16 }), 'Create project');
  const form = h(
    'form',
    {
      id: 'create-project-form',
      novalidate: true,
      onsubmit: sendWith(problem, submit, async () => {
        const made = await createProject(name.value);
        location.assign(projectAddress(made));
      }),
    },
    labelled('project-name', 'Project name', name),
    problem,
    submit,
  );
  put(
    main,
    h(
      'div',
      { class: 'entry-stack' },
      h(
        'section',
        { class: 'entry-section', 'aria-labelledby': 'projects-title' },
        h('header', { class: 'entry-head' }, h('h1', { id: 'projects-title' }, 'Your projects')),
        found.length
          ? h(
              'ul',
              { id: 'projects', class: 'plain projects' },
              found.map((project) =>
                h(
                  'li',
                  { class: 'project', dataset: { project: project.id } },
                  h('span', { class: 'avatar', 'aria-hidden': 'true' }, initial(project.name)),
                  h('span', { class: 'project-info' }, h('a', { class: 'open-project', href: projectAddress(project) }, project.name), h('span', { class: 'project-meta' }, roleChip(project.role), h('span', { class: 'quiet' }, `Created ${moment(project.created_at)}`))),
                  icon('arrow_right', { size: 16, className: 'project-go' }),
                ),
              ),
            )
          : h('p', { id: 'no-projects', class: 'empty' }, `Welcome, ${session.account.name}. You have no projects yet.`),
      ),
      h('section', { class: 'entry wide', 'aria-labelledby': 'new-project-title' }, h('h2', { id: 'new-project-title' }, 'New project'), form),
    ),
  );
  name.focus();
}

// A page that says a project cannot be opened, and where to go instead.
export function showNoProject(main, message) {
  put(
    main,
    h(
      'section',
      { class: 'entry' },
      heading('alert', 'That project is not one of yours'),
      h('p', { id: 'no-such-project', role: 'alert' }, message),
      h('p', {}, h('a', { href: '/board', id: 'back-to-projects' }, 'Back to your projects')),
    ),
  );
}
