# Blocked: questions for the owner

Product decisions that `SPEC.md` does not answer. Each has a recommendation and the default taken
meanwhile (nothing here stops a milestone). Answer one by editing it, or tell the loop.

## M15: email verification

**Answered by M24, see the update under "account recovery" below.**

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

**Update 2026-11-17.** The owner asked for email invitations, which needs a way to send mail. That is M24 in `SPEC.md` (optional, off by default); with it, email verification and account recovery become possible too.

**Answered by M24 (2026-11-18).** Built as option 2 for both questions, optional and off by default: with a mail server set (`docs/configuration.md`), a new account is sent a confirmation link (confirming is not required to use the account), and the sign-in page offers "Forgot your password?", which emails a link that works once for one hour and ends every session when used. Without a mail server nothing changes: no verification, and no recovery. The command for whoever runs the server (option 3) is still not built, so a server without mail has no way back for a forgotten password.

## M21: how the Graph should work for a person

**Question.** The whole-project map (one force-directed picture of every epic, story, task,
requirement, decision and fact) is not useful to a person: at 245 records it is an unreadable ball.
What should the Graph page be instead?

**Why it is open.** The owner asked for research and a proposal, not a build. The research (Obsidian's
local and global graph, Neo4j Bloom, Kumu, Linear and Jira dependency views, Log4brains, the graph
drawing literature) says a whole-graph map is an audit tool, and that useful graph views start from
one item, show one or two steps around it with typed and labelled lines, are paired with a grouped
list, and draw cross-links only for the chosen item. The owner's own idea (leaves pushed to the
outside so no lines cross) is the radial tree layout, which is crossing-free for the hierarchy only.
The full proposal is in the session notes (`m21-graph-proposal.md`).

**Options.**

1. **Around-first Graph (the proposal).** The Graph opens on a question-led workspace: a focus bar,
   then four views (Around, Structure as a radial tree with leaves outside, Order as a layered
   dependency chain, Timeline of decisions and facts), a Check-up strip of counts, saved views, and
   the existing lists. The old whole-project map stays only as an aggregated "Whole project (for
   checking)".
2. **Keep the map, tame it.** Keep the force map as the first view but start with one epic, label every
   dot, and add the radial layout as a switch.
3. **Remove the map.** Keep only the Work and Context lists and the item pages.

**Recommendation.** Option 1, built in two slices: Around with the grouped list first, then the
other views. A clickable mockup from the demo data comes before the real build.

**Default taken.** Option 3 is not taken. Nothing is changed until the owner answers: the Graph stays as
it is in M20.

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

## M22: several people and several agents on one project

**Question.** Today a project has one owner. Names such as "decided by", "recorded by" and "claimed by"
are typed text, and an agent names itself on every call, so nothing proves who did what. Should
Vrum have members, roles and verified identities for people and for agents?

**Why it is open.** Shared projects are a non-goal in `SPEC.md`, so nothing in the spec answers it.
Taiga (the owner's comparison) is human-first: a project has members with roles and permissions,
invited by email, and an agent signs in as an ordinary user (the Taiga MCP uses one username and
password that reaches every project of that user). Vrum already has two kinds of credential
(account sessions for people, project keys for agents) but the recorded actor is only a string.

**Options.**

1. **Not yet.** One owner per project; typed names stay unverified.
2. **People and agents as identities (recommended).** (a) Members: the owner invites people with a
   copyable invite link (Vrum sends no email), each with their own account; roles Owner, Editor,
   Viewer. (b) Every agent key is one named agent identity, and the server stamps the actor from the
   key, so a claim or a change cannot be made in another agent's name. (c) Every human action is
   stamped from the signed-in account, replacing the typed "Recorded as" name. (d) The board shows
   whether an actor is a person or an agent. (e) Confirming a fact or decision needs a person
   identity, which an agent key cannot give.
3. **Taiga-style.** Custom roles with permission sets, invitations by email, agents as ordinary
   users with a shared login.

**Recommendation.** Option 2 as its own milestone, in two slices: first the identities (agent
identity from the key, person from the session, the kind shown on the board), then members and roles.

**Answered 2026-11-17.** The owner chose option 2 as the next big milestone after the Graph: M22 in
`SPEC.md`. M23 (what each agent key may do) follows it.

## M25: trying sign-in with a real provider

**Question.** When do you want to try sign-in with a real provider (GitHub, Google, Microsoft, Okta, or your own
LDAP directory) instead of the test stand-ins?

**Why it is open.** Everything was proved against a real OpenLDAP, a real Dex OpenID provider and stand-ins for
GitHub, but GitHub and the company providers need an app that only you can register (a client id and secret, and
the redirect address `<VRUM_PUBLIC_URL>/board/auth/<oidc or github>/callback`). `docs/configuration.md` has the steps
and example settings for each. Nothing in Vrum creates such an app or a user for you.

**Default taken.** Nothing is tried live; the provider settings stay empty and sign-in is as before.

## M26: requirements in `recall` and in Around

**Question.** Should `recall` also return the requirements of an item's story, and should the Graph's Around tab show a task's story requirements at one step?

**Why it is open.** Decisions and facts now come down the parent chain in `recall` and in Around (M26), and `next_work` has always given an agent the requirements of its story, but `recall` has no requirements section, and Around shows a story's requirements only when the story is the focus (a task or epic only at two steps, marked "via"). So Around's Agent view and `recall` still differ from `next_work` for requirements. The owner asked on 2026-11-18 why requirements are not shown there.

**Options.**

1. **Leave it.** Requirements stay on the item pages ("Requirements of its story") and in `next_work`.
2. **Add them (recommended).** `recall` gets a `requirements` section (a task or story: the rules of its story; an epic: the rules of its stories, paged; a requirement: itself is not repeated), each saying where it comes from, and Around shows a task's story requirements at one step marked "via S-1". Epics keep their stories' requirements at two steps in the picture because there can be many.

**Default taken.** Option 1 until the owner decides; nothing is built.

**Answered 2026-11-18 (option 2, built the same day as part of M26).** The owner asked why Around did not show a task's requirements and whether requirements should be folded into facts. They are not facts (a requirement belongs to one story, can be edited, has a status and feeds Coverage; a fact is never edited), so they stay. `recall` now has a `requirements` section: a story's own, and a task's story's, each saying where it comes from, paged with `show=requirements`; dropped ones are left out, as in `next_work`. An epic has none of its own and gets none from its stories, to keep to "it goes down only" (Around still shows an epic's stories' requirements at two steps). Around shows a task's story requirements at one step, marked "via S-1".

## M35: what to remove, merge and calm down

**Question.** The owner asked (2026-11-20) for a sweep of everything the app shows twice and of what to simplify, so
that people are not overwhelmed (for example, how many labels one task carries on the Board) while agents keep a trusted
context. What should be taken away?

**Why it is open.** It changes what every page shows and moves tests, so it is a product decision. The sweep is in the
session notes (`m35-audit.md`: 40 repeats, with a fix for each, and the measurements). The worst cases:

- A Board card carries 4.0 chips on average (17 of 180 have 6 or 7). "Normal" priority shows on 110 of 180 and a "Done" or
  "Dropped" chip sits inside the Done and Dropped columns on 47 of 47.
- A task's link to what it waits for is drawn three times on its page (header strip, Links card, Relations table).
- The page has a jump bar and then the same section names again as headings, plus Created, Changed and Version, which are
  the first and last rows of History.
- The sidebar counts do not match the pages (Board 233, but the Board shows 180 tasks; Graph and Lists both say 430).
- The same claims are listed twice on Activity, and "Connect an agent" is on both Users and Tools.
- Overdue has three looks (red alert chip, amber clock chip, red hatching), and a criterion or note is named five ways
  (`Acceptance criterion #3`, `Criterion #3`, `AC-3`, `#28`, `N-7`).

**Options.**

1. **Remove the repeats only (tier 1).** Delete what is shown twice and fix the names and looks. Nothing is rearranged.
2. **Also calm the Board card and the item page (tier 2).** A card is its code and title plus at most three quiet signals
   (priority only when High or Urgent, size, a red date only when late, one merged progress sign, a holder avatar, icons
   for Waiting, Blocked and Handoff). An epic lane is title, code and "14/35". An item page is one scroll with a quiet row
   of properties, no jump bar, one Links section (typed groups and backlinks) and empty sections hidden. Everything taken
   off a face stays one hover or click away, so nothing an agent writes becomes invisible.
3. **Also merge the navigation (tier 3).** One Work page with a Board, List and Roadmap switch sharing one filter; Coverage
   becomes saved filters in the List; Acceptance criteria, Notes and Possible duplicates become one Notes page; the
   Everything list goes; and ⌘K is the one finder. Navigation becomes Work, Graph, Notes, Activity, Settings.

**Recommendation.** Options 1 and 2 together as M35, about the size of M34. Option 3 after a clickable mockup from the
demo data, as with the Graph in M21. The MCP tools do not change for any option; the board keeps calling the same ones.

**Default taken.** Nothing is changed until the owner answers.
