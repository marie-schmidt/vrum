# Blocked: questions for the owner

Product decisions that `SPEC.md` does not answer. Each has a recommendation and the default taken
meanwhile (nothing here stops a milestone). Answer one by editing it, or tell the loop.

## M15: email verification

**Question.** Should the email address of a new account be checked (a link sent to it, to be
opened before the account can be used)?

**Why it is open.** The spec says email verification must be settled here before it is built, and
that it must not be answered by an unconfigured email service. Vrum has no way to send email: there
is no mail server setting, and inventing one would be a service nobody has set up.

**Options.**

1. **No verification.** An address is only the unique name a person signs in with. Anyone can
   register any address; nobody can read anyone else's projects, because the password and the
   project's ownership are what protect them.
2. **A link sent by email.** Needs outgoing mail settings (an SMTP host, a sender address and its
   credentials) added to the environment, and a way to test it that is not a real inbox.
3. **The owner of the server approves each account.** No mail needed, but it needs a place for the
   owner to do it, and it does not suit a person registering on their own machine.

**Recommendation.** Option 1 until the owner has a mail service to point Vrum at; then option 2.

**Default taken.** Option 1: nothing is verified, and the README says so. Nothing in the code
depends on an address being real.

## M15: account recovery

**Question.** What happens when a person forgets their password?

**Why it is open.** The spec says recovery must be settled here before it is built. The usual way
is an emailed link, which needs the mail service above.

**Options.**

1. **No recovery.** A forgotten password means a new account; the old account's projects stay where
   they are, out of reach.
2. **An emailed reset link.** Needs outgoing mail (see above).
3. **A command for whoever runs the server.** A script that sets a new password for an address,
   run with access to the server's database. No mail needed, and it fits a prototype that one
   person runs on their own machine. It would have to ask for the password without echoing it and
   never write it anywhere.

**Recommendation.** Option 3 now, option 2 when there is mail.

**Default taken.** Option 1: no recovery is built, and signing in again is the only way back.

## M15: changing a password or an email, and deleting an account or a project

**Question.** Should a person be able to change their password or email, delete a project, or
delete their account?

**Why it is open.** The spec lists registering, signing in and out, creating and switching
projects, and agent keys. It does not list these. Deleting is the one with consequences: it throws
away a graph, its memory and its history, which Vrum otherwise never does (nothing in it is ever
deleted; work is dropped and decisions are replaced).

**Options.**

1. **Not yet.** Nothing is deleted or changed after it is made, except a key, which is revoked.
2. **Change a password and a display name; no deletion.**
3. **All of it, with a typed confirmation for deleting.**

**Recommendation.** Option 2 as the next small step, and option 3 only when someone asks for it.

**Default taken.** Option 1.
