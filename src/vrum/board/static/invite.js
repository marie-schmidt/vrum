// The page of an invitation's link (/board/invite/<token>). It says what the person would join (the
// project, its owner, the role they would have and who invited them) and offers the one next step:
// join. The server decides everything: a link that is used, expired, withdrawn or never was gets one
// plain answer, and a person who owns the project, is in it already or has a name someone in it
// already uses is told so before they press anything. Whatever is refused is shown in the server's
// words, with the way back to the person's projects.
//
// Someone who is not signed in sees the sign-in and registration form instead (app.js), with a line
// saying why, and comes back to this page afterwards.

import { NotSignedIn, Refusal, acceptInvite, previewInvite, renameAccount, signOut } from './api.js';
import { account } from './ctx.js';
import { h, moment, put } from './dom.js';
import { heading } from './entry.js';
import { field, refuse } from './forms.js';
import { icon } from './icons.js';
import { ROLES, actorLabel, asRole, roleChip } from './labels.js';

export const INVITE_INTRO = 'You have been invited to a project. Sign in or create an account to see it.';

// An invitation that was emailed to an address can only be accepted by an account with that address.
const forAnotherAddress = (words) => /only an account with that address/i.test(words || '');

const back = () => h('p', { class: 'entry-switch' }, h('a', { href: '/board', id: 'back-to-projects' }, 'Back to your projects'));
const card = (...children) => h('div', { class: 'entry-wrap' }, h('section', { class: 'entry invite', id: 'invite' }, ...children));

export async function showInvite(main, token) {
  document.title = 'Invitation · Vrum';
  put(main, h('p', { class: 'loading', role: 'status' }, 'Looking at the invitation…'));
  let invite;
  try {
    invite = await previewInvite(token);
  } catch (error) {
    if (error instanceof NotSignedIn || !(error instanceof Refusal)) throw error;
    put(main, card(heading('alert', 'This invitation cannot be used'), h('p', { id: 'invite-problem', class: 'problem', role: 'alert' }, error.message), back()));
    return;
  }
  draw(main, token, invite);
}

function draw(main, token, invite) {
  const problem = h('p', { id: 'invite-problem', class: 'problem', role: 'alert' });
  const join = h(
    'button',
    {
      type: 'button',
      id: 'accept-invite',
      class: 'primary',
      onclick: async () => {
        problem.replaceChildren();
        join.disabled = true;
        try {
          const joined = await acceptInvite(token);
          location.assign(`/board/p/${joined.id}#/board`);
        } catch (error) {
          if (error instanceof NotSignedIn) {
            location.reload();
            return;
          }
          if (!(error instanceof Refusal)) throw error;
          refuse(problem, error.message);
          join.disabled = false;
          showRename(error.message);
        }
      },
    },
    icon('check', { size: 16 }),
    `Join ${invite.project_name} as ${asRole(invite.role)}`,
  );
  // A name someone in the project already uses is the one refusal that the person can mend here, so
  // the form for it is on this page and not on a page of a project they cannot open yet.
  const renaming = h('div', { id: 'invite-rename' });
  const showRename = (words) => {
    if (!/name/i.test(words || '') || forAnotherAddress(words)) return;
    const name = h('input', { type: 'text', id: 'invite-name', name: 'name', autocomplete: 'name', value: account.name });
    const trouble = h('p', { class: 'problem', role: 'alert', id: 'invite-name-problem' });
    put(
      renaming,
      h(
        'form',
        {
          class: 'setting-form invite-rename',
          novalidate: true,
          onsubmit: async (event) => {
            event.preventDefault();
            trouble.replaceChildren();
            try {
              const made = await renameAccount(name.value);
              account.name = made.name;
              await showInvite(main, token);
            } catch (error) {
              if (!(error instanceof Refusal)) throw error;
              refuse(trouble, error.message);
            }
          },
        },
        field('Your name', name, 'Choose one nobody in the project uses.'),
        h('button', { type: 'submit', id: 'invite-save-name' }, 'Change my name'),
        trouble,
      ),
    );
  };
  if (invite.problem) {
    refuse(problem, invite.problem);
    showRename(invite.problem);
  }
  const wrongAddress = Boolean(invite.problem) && forAnotherAddress(invite.problem);
  const nameProblem = Boolean(invite.problem) && !wrongAddress && /name/i.test(invite.problem);
  const otherAccount = async () => {
    await signOut();
    location.reload();
  };
  put(
    main,
    card(
      heading('person', nameProblem ? `Change your name to join ${invite.project_name}` : wrongAddress ? 'This invitation is for another email address' : invite.problem ? `You cannot join ${invite.project_name} with this invitation` : `Join ${invite.project_name}`),
      h(
        'dl',
        { class: 'invite-facts' },
        h('dt', {}, 'Owner'),
        h('dd', {}, actorLabel(invite.owner_name, 'person')),
        h('dt', {}, 'Your role'),
        h('dd', { id: 'invite-role' }, roleChip(invite.role), h('span', { class: 'quiet' }, ROLES[invite.role]?.can || '')),
        h('dt', {}, 'Invited by'),
        h('dd', {}, actorLabel(invite.invited_by, 'person')),
        h('dt', {}, 'Good until'),
        h('dd', {}, moment(invite.expires_at)),
      ),
      problem,
      renaming,
      invite.problem ? null : join,
      h(
        'p',
        { class: 'quiet invite-who' },
        `You are signed in as ${account.name} (${account.email}).`,
        wrongAddress ? null : ' ',
        wrongAddress ? null : h('button', { type: 'button', class: 'link', id: 'invite-other-account', onclick: otherAccount }, 'Sign in as someone else'),
      ),
      wrongAddress ? h('button', { type: 'button', class: 'primary', id: 'invite-other-account', onclick: otherAccount }, icon('person', { size: 16 }), 'Sign in with a different account') : null,
      back(),
    ),
  );
}
