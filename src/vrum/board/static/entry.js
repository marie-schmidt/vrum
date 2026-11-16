// What the board shows before a project is open: signing in, creating an account, and the person's
// own projects. Nothing here is checked in the browser: each form sends what was typed and shows
// the server's refusal in its own words, so the page and an API client are told the same thing.

import { Refusal, createProject, projects, register, signIn } from './api.js';
import { setActor } from './ctx.js';
import { h, moment, put } from './dom.js';

const projectAddress = (project) => `/board/p/${project.id}#/board`;

function labelled(id, label, control, help = null) {
  const note = help ? h('p', { class: 'quiet', id: `${id}-help` }, help) : null;
  if (note) control.setAttribute('aria-describedby', note.id);
  return h('div', { class: 'field' }, h('label', { for: id }, label), control, note);
}

// Send a form's work, and show a refusal under it. The buttons stay off while it goes.
function sendWith(problem, button, work) {
  return async (event) => {
    event.preventDefault();
    problem.textContent = '';
    button.disabled = true;
    try {
      await work();
    } catch (error) {
      if (!(error instanceof Refusal)) throw error;
      problem.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
}

// The sign-in and registration page. `onSignedIn(session)` is called once the server has signed
// the person in, whichever of the two they used.
export function showSignIn(main, { onSignedIn, message = '', registering = false }) {
  const draw = (creating) => {
    const email = h('input', { type: 'text', inputmode: 'email', id: 'account-email', name: 'email', autocomplete: creating ? 'email' : 'username', spellcheck: 'false' });
    const password = h('input', { type: 'password', id: 'account-password', name: 'password', autocomplete: creating ? 'new-password' : 'current-password' });
    const name = creating ? h('input', { type: 'text', id: 'account-name', name: 'name', autocomplete: 'name' }) : null;
    const problem = h('p', { class: 'problem', role: 'alert', id: 'entry-problem' }, message);
    const submit = h('button', { type: 'submit', class: 'primary', id: creating ? 'register-submit' : 'sign-in-submit' }, creating ? 'Create account' : 'Sign in');
    const done = async (session) => {
      // Whoever signs in is the name that changes are recorded with, until they say otherwise.
      setActor(session.account.name);
      await onSignedIn(session);
    };
    const form = h(
      'form',
      {
        id: creating ? 'register-form' : 'sign-in-form',
        novalidate: true,
        onsubmit: sendWith(problem, submit, async () =>
          done(creating ? await register(email.value, name.value, password.value) : await signIn(email.value, password.value)),
        ),
      },
      name ? labelled('account-name', 'Your name', name, 'Every change you make is recorded with this name, next to the agents’ names.') : null,
      labelled('account-email', 'Email', email),
      labelled('account-password', 'Password', password, creating ? 'At least 10 characters. A few unrelated words make a good one.' : null),
      problem,
      submit,
    );
    put(
      main,
      h(
        'section',
        { class: 'entry' },
        h('h1', {}, creating ? 'Create an account' : 'Sign in to Vrum'),
        h('p', { class: 'help' }, creating ? 'An account holds your own private projects. Nobody else can see them.' : 'Use the email and password of your account.'),
        form,
        creating
          ? h('p', {}, 'Have an account already? ', h('button', { type: 'button', class: 'link', id: 'show-sign-in', onclick: () => draw(false) }, 'Sign in'))
          : h('p', {}, 'New here? ', h('button', { type: 'button', class: 'link', id: 'show-register', onclick: () => draw(true) }, 'Create an account')),
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
  const submit = h('button', { type: 'submit', class: 'primary', id: 'create-project' }, 'Create project');
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
    labelled('project-name', found.length ? 'Name of a new project' : 'Name of your first project', name, 'A project is a private workspace with its own work, requirements, decisions and facts.'),
    problem,
    submit,
  );
  put(
    main,
    h(
      'section',
      { class: 'entry' },
      h('h1', {}, 'Your projects'),
      found.length
        ? h(
            'ul',
            { id: 'projects', class: 'plain projects' },
            found.map((project) =>
              h(
                'li',
                { class: 'project', dataset: { project: project.id } },
                h('a', { class: 'open-project', href: projectAddress(project) }, project.name),
                h('span', { class: 'quiet' }, ` made ${moment(project.created_at)}`),
              ),
            ),
          )
        : h('p', { id: 'no-projects', class: 'empty' }, `Welcome, ${session.account.name}. You have no projects yet. Create one to start: it opens its own board.`),
      form,
    ),
  );
  name.focus();
}

// A page that says a project cannot be opened, and where to go instead.
export function showNoProject(main, message) {
  put(
    main,
    h('section', { class: 'entry' }, h('h1', {}, 'That project is not one of yours'), h('p', { id: 'no-such-project', role: 'alert' }, message), h('p', {}, h('a', { href: '/board', id: 'back-to-projects' }, 'Back to your projects'))),
  );
}
