// The Members section of the Users page: the people of the project, and for its owner the way to
// invite more, to change what each may do and to take someone out.
//
//   Everyone  sees who is in the project (the owner also sees the email addresses), and a person who
//             is not the owner can leave it.
//   The owner makes an invitation: a link, shown once, that works once and for seven days. Without
//             email, the owner sends the link to the person. When the server can send email, the
//             owner may give an address as well: the link is emailed to it, and only that address can
//             accept it. Such an invitation can be sent again (the old link then stops). The
//             invitations still waiting are listed and can be withdrawn.
//
// What a role lets a person do is decided by the server on every request. The board only keeps from
// offering what it would refuse (see `can` in ctx.js).

import { Refusal, Stale, changeRole, createInvite, invites, mailOn, members, projectId, projectInfo, removeMember, resendInvite, revokeInvite } from './api.js';
import { copyButton } from './copy.js';
import { account, can, notify, ui } from './ctx.js';
import { h, moment, put } from './dom.js';
import { dialogForm, field, openDialog, refuse } from './forms.js';
import { icon } from './icons.js';
import { ROLES, actorLabel, asRole, roleChip } from './labels.js';

let section = null;
let people = null; // the members, or null until they have been read
let waiting = []; // the invitations that can still be accepted (the owner's to see)
let projectName = '';
let fresh = null; // the invitation just made ({ invite, token }), shown until the person leaves the page
let revealed = false; // the new invitation has been brought into view
let told = ''; // the role the person was last told about, so that a change is said once
let generation = 0;

const GIVEN = ['editor', 'viewer'];

export async function mount(element) {
  section = element;
  people = null;
  waiting = [];
  fresh = null;
  revealed = false;
  told = '';
  const mine = ++generation;
  draw();
  await load(mine);
}

export function unmount() {
  generation += 1;
  section = null;
  people = null;
  waiting = [];
  fresh = null;
}

// News came in: who is in the project may have changed.
export function refresh() {
  if (section) load(generation).catch(() => {});
}

async function load(mine) {
  let read;
  try {
    read = await Promise.all([projectInfo(projectId()), members(), can('manage') ? invites() : []]);
  } catch (error) {
    if (mine !== generation || !section || error instanceof Stale) return;
    if (!(error instanceof Refusal)) throw error;
    const failed = h('p', { class: 'problem', role: 'alert' });
    refuse(failed, error.message);
    put(section, h('h2', { id: 'users-members-title' }, 'Members'), failed);
    return;
  }
  if (mine !== generation || !section) return;
  const [project, list, pending] = read;
  projectName = project.name;
  people = list;
  waiting = pending;
  // The role is read again with the people. If it is not what the page was drawn for, say so once:
  // the person's controls follow when the page is loaded again.
  const now = people.find((one) => one.account_id === account.id)?.role;
  if (now && now !== ui.role && told !== now) {
    told = now;
    notify(`Your role in this project is now ${asRole(now)}. Load the page again to see what that lets you do.`);
  }
  draw();
}

// -- drawing it --------------------------------------------------------------------------------------------

function draw() {
  if (!section) return;
  const owner = can('manage');
  const loaded = people !== null;
  put(
    section,
    h(
      'div',
      { class: 'section-head' },
      h('h2', { id: 'users-members-title' }, 'Members', loaded ? h('span', { class: 'heading-count' }, ` (${people.length})`) : null),
      h('div', { class: 'section-actions', id: 'members-actions' }, owner && loaded ? h('button', { type: 'button', id: 'invite-open', class: 'primary', onclick: inviteDialog }, icon('plus', { size: 16 }), 'Invite someone') : null),
    ),
    fresh ? revealCard() : null,
    loaded ? h('ul', { id: 'members', class: 'plain members' }, people.map(memberRow)) : h('p', { class: 'loading', role: 'status' }, 'Loading the members…'),
    loaded && owner ? invitationsBlock() : null,
  );
  if (fresh && !revealed) {
    revealed = true;
    const card = section.querySelector('#new-invite');
    card.tabIndex = -1;
    card.focus();
  }
}

function memberRow(member) {
  const you = member.account_id === account.id;
  const owner = can('manage');
  const own = member.role === 'owner';
  const role = h(
    'select',
    { id: `role-${member.account_id}`, class: 'role-select small', 'aria-label': `What ${member.name} can do`, onchange: () => changeTo(member, role), value: member.role },
    GIVEN.map((value) => h('option', { value }, ROLES[value].label)),
  );
  return h(
    'li',
    { class: 'member', dataset: { member: member.account_id, role: member.role, you: you ? '1' : '0' } },
    h(
      'div',
      { class: 'member-main' },
      h('strong', { class: 'member-name' }, actorLabel(member.name, 'person', { size: 16 }), you ? h('span', { class: 'chip you' }, 'You') : null),
      member.email ? h('span', { class: 'quiet member-email' }, member.email) : null,
    ),
    h(
      'div',
      { class: 'member-side' },
      h('span', { class: 'member-role' }, owner && !own ? role : roleChip(member.role)),
      h('span', { class: 'quiet member-since' }, `Joined ${moment(member.joined_at)}`),
      h(
        'span',
        { class: 'member-action' },
        owner && !own ? h('button', { type: 'button', class: 'remove-member small danger', onclick: () => removeDialog(member) }, 'Remove…') : null,
        you && !own ? h('button', { type: 'button', id: 'leave-project', class: 'small danger', onclick: leaveDialog }, 'Leave this project…') : null,
      ),
    ),
  );
}

async function changeTo(member, select) {
  const before = member.role;
  select.disabled = true;
  try {
    const done = await changeRole(member.account_id, select.value);
    notify(`${member.name} is now ${asRole(done.role)}.`);
    await load(generation);
  } catch (error) {
    select.value = before;
    select.disabled = false;
    if (error instanceof Stale) return;
    if (!(error instanceof Refusal)) throw error;
    notify(error.message, { refusal: true });
  }
}

function removeDialog(member) {
  const { form } = dialogForm({
    fields: [h('p', {}, `Remove ${member.name} from ${projectName}? They lose access to it at once, and have to be invited again to come back. What they recorded stays in the history under their name.`)],
    submit: 'Remove member',
    work: async () => {
      await removeMember(member.account_id);
      form.closest('dialog')?.close();
      notify(`${member.name} is no longer in ${projectName}.`);
      await load(generation);
    },
  });
  openDialog('Remove a member', [form]);
}

function leaveDialog() {
  const { form } = dialogForm({
    fields: [h('p', {}, `Leave ${projectName}? You will not see it any more, and the owner has to invite you again to come back. What you recorded stays in the history under your name.`)],
    submit: 'Leave project',
    work: async () => {
      await removeMember('me');
      location.assign('/board');
    },
  });
  openDialog('Leave this project', [form]);
}

// -- invitations ---------------------------------------------------------------------------------------------

function inviteDialog() {
  // What the person can do is a decision, so no role is chosen when the dialog opens.
  const choices = GIVEN.map((value) =>
    h(
      'label',
      { class: 'role-choice', for: `invite-role-${value}` },
      h('input', { type: 'radio', name: 'role', value, id: `invite-role-${value}` }),
      h('span', { class: 'role-choice-text' }, h('strong', {}, ROLES[value].label), h('span', { class: 'quiet' }, ROLES[value].can)),
    ),
  );
  // The address is only asked for when the server can send email: otherwise there is nothing to do with it.
  const email = mailOn() ? h('input', { type: 'text', inputmode: 'email', id: 'invite-email', name: 'email', autocomplete: 'off', spellcheck: 'false' }) : null;
  const { form } = dialogForm({
    fields: [
      h('fieldset', { class: 'role-choices' }, h('legend', {}, 'What can the person do?'), choices),
      email ? field('Email address (optional)', email, 'The link is emailed to this address, and only it can accept the invitation.') : null,
    ],
    submit: 'Create invitation link',
    work: async () => {
      const chosen = choices.map((label) => label.querySelector('input')).find((input) => input.checked);
      if (!chosen) throw new Refusal('Choose what the person can do.');
      fresh = { ...(await createInvite(chosen.value, email ? email.value.trim() : '')), resent: false };
      revealed = false;
      form.closest('dialog')?.close();
      await load(generation);
    },
  });
  if (email) {
    const submit = form.querySelector('button[type="submit"]');
    email.addEventListener('input', () => {
      submit.textContent = email.value.trim() ? 'Send invitation' : 'Create invitation link';
    });
  }
  const box = openDialog('Invite someone', [form]);
  // The address, when it is asked for, is the first thing to type in; otherwise the dialog itself has the focus, so that no role is lit.
  if (email) email.focus();
  else {
    box.tabIndex = -1;
    box.focus();
  }
}

const sentence = (text) => (/[.!?]$/.test(text) ? text : `${text}.`);

// The link of the invitation just made. It is shown once, because only a hash of it is kept. When
// an address was given, it also says whether the email went.
function revealCard() {
  const { invite, token, emailed, email_problem: trouble, resent } = fresh;
  const to = invite.sent_to;
  const link = h('code', { id: 'invite-link', class: 'secret' }, `${location.origin}/board/invite/${token}`);
  const mailed = emailed
    ? h('p', { class: 'mail-note', id: 'invite-emailed', role: 'status' }, icon('mail', { size: 16 }), h('span', {}, `Emailed to ${to}.`))
    : null;
  const failed = to && !emailed ? h('p', { class: 'problem', role: 'alert', id: 'invite-email-problem' }) : null;
  if (failed) refuse(failed, `The email to ${to} was not sent. ${sentence(trouble || 'The mail server could not be reached.')}`);
  let help = 'It works once and for 7 days.';
  if (mailOn() && to) help = `${emailed ? 'The link is in the email too. ' : 'Send this link to the person yourself. '}It works once and for 7 days, and only ${to} can accept it.`;
  else if (mailOn()) help = 'Send this link to the person. It works once and for 7 days.';
  return h(
    'section',
    { id: 'new-invite', class: 'key-reveal', 'aria-labelledby': 'new-invite-title' },
    h('h3', { id: 'new-invite-title' }, icon('link', { size: 18 }), resent ? `A new link for the invitation to ${to}` : `An invitation for ${asRole(invite.role)}`),
    mailed,
    failed,
    h('p', { class: 'help' }, icon('alert', { size: 16 }), 'Copy it now. It is not shown again.'),
    h('div', { class: 'secret-line' }, link, copyButton(link, 'Copy the invitation link')),
    h('p', { class: 'quiet' }, `${resent ? 'The link sent before no longer works. ' : ''}${help}`),
  );
}

function invitationsBlock() {
  if (!waiting.length) return null;
  return h(
    'div',
    { class: 'subsection', id: 'invitations' },
    h('h3', { id: 'invites-title' }, 'Invitations waiting', h('span', { class: 'heading-count' }, ` (${waiting.length})`)),
    h('ul', { id: 'invites', class: 'plain invites', 'aria-labelledby': 'invites-title' }, waiting.map(inviteRow)),
  );
}

function inviteRow(invite) {
  return h(
    'li',
    { class: 'invite-row', dataset: { invite: invite.id, role: invite.role } },
    h(
      'div',
      { class: 'invite-main' },
      h('div', { class: 'invite-line' }, roleChip(invite.role), h('code', { class: 'hint-code' }, `${invite.hint}…`)),
      invite.sent_to ? h('span', { class: 'invite-sent' }, icon('mail', { size: 14 }), invite.sent_at ? `Sent to ${invite.sent_to}, ${moment(invite.sent_at)}` : `For ${invite.sent_to}. The email was not sent.`) : null,
      h('span', { class: 'quiet' }, invite.sent_to ? `Good until ${moment(invite.expires_at)}` : `Created ${moment(invite.created_at)} by ${invite.created_by}; good until ${moment(invite.expires_at)}`),
    ),
    h(
      'div',
      { class: 'invite-actions' },
      mailOn() && invite.sent_to ? h('button', { type: 'button', class: 'resend-invite small', onclick: () => resendDialog(invite) }, icon('mail', { size: 14 }), 'Resend…') : null,
      h('button', { type: 'button', class: 'revoke-invite small danger', onclick: () => revokeDialog(invite) }, 'Withdraw…'),
    ),
  );
}

function resendDialog(invite) {
  const { form } = dialogForm({
    fields: [h('p', {}, `Send this invitation to ${invite.sent_to} again? A new link is made and emailed, and the link sent before stops working. The new one is good for 7 days from now.`)],
    submit: 'Resend invitation',
    work: async () => {
      fresh = { ...(await resendInvite(invite.id)), resent: true };
      revealed = false;
      form.closest('dialog')?.close();
      await load(generation);
    },
  });
  openDialog('Resend an invitation', [form]);
}

function revokeDialog(invite) {
  const { form } = dialogForm({
    fields: [h('p', {}, `Withdraw this invitation for ${asRole(invite.role)}? The link stops working at once. Anyone who has it is told it cannot be used.`)],
    submit: 'Withdraw invitation',
    work: async () => {
      await revokeInvite(invite.id);
      form.closest('dialog')?.close();
      notify('The invitation is withdrawn.');
      await load(generation);
    },
  });
  openDialog('Withdraw an invitation', [form]);
}
