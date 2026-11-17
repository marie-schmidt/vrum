# The board

The board is for people. It is at `http://127.0.0.1:8000/board`, served by the same container and
on the same port as the agents' endpoint, and it shows and changes the same graph. Whatever an
agent does appears on it within a few seconds with no reload, and whatever you do on it is what an
agent reads next. It keeps no data of its own and loads nothing from the internet.

This page describes every part of it. For a first look, see the README's
[Getting started](../README.md#getting-started).

## Accounts and projects

- **An account** is an email address and a password that Vrum keeps itself. The password is stored
  only as a salted, slow hash (scrypt), never in plain text. Nothing is verified by email, and a
  forgotten password cannot be recovered yet (see `BLOCKED.md`: both wait for an answer from the
  owner, since Vrum has no way to send email). Several accounts may share one install.
- **Registering** asks for a name, an email address and a password of 10 characters or more. The
  name is what your changes are recorded with, next to the agents' names. Every refusal says what
  is wrong, and a wrong password and an address with no account get the same answer.
- **Signing in** gives the browser a session: a cookie the page's scripts cannot read, that expires
  after 12 hours and ends the moment you sign out. Every change from the board also carries a secret
  that only the board's own page knows and is checked against where it came from, so another
  website cannot make your browser change anything. Failed sign-ins are slowed down: five wrong
  passwords for one address stop that address for ten minutes.
- **A project** is a private workspace: its own epics, stories and tasks, requirements, decisions,
  facts, claims, history and search. After signing in you see your own projects, with a box to
  create the first one when you have none. Each project numbers its own items from 1, and each kind
  on its own: a project can have epic 1, story 1 and task 1, shown as E-1, S-1 and T-1. Item S-12 in
  one project is not S-12 in another. Nothing is shared between projects, and a project can only
  be reached by its owner and by the agents the owner gave a key to. The database itself enforces
  this: every row belongs to one project and cannot refer to a row of another.
- **A project's address** is `/board/p/<number>`. An address copied to another account opens
  nothing: it is refused in the same words as a project that does not exist.
- **Admins and backups** stay outside accounts. `./scripts/backup.sh` and `./scripts/restore.sh`
  work on the whole database, which holds everything of everyone (see
  [backup and export](backup-and-export.md)).
- The server answers only to requests addressed to `localhost`, and compose publishes it on
  `127.0.0.1` only. That is right for one machine. Passwords and keys travel as plain text over
  HTTP: to use it from another machine, put it behind something that adds TLS (the session cookie
  is then marked Secure), and read `DECISIONS.md` (the M6 and M15 sections) first.

## The layout

A project has three views: **Board**, **Graph** and **Roadmap**. A project opens on the Board.

- **The sidebar** (left) holds the project switcher, the three views with how much each holds,
  the epics of the project, and, at the foot, **Activity**, the **Settings & tools** menu, the
  appearance control and your account. The numbers beside the views are the stories and tasks on the
  Board, the work items and records in force in the Graph, and the items with dates on the Roadmap.
  Choosing an epic in the list opens the Board on that epic. Choosing another project in the
  switcher loads that project's board afresh, so nothing of the first can appear in the second.
  **Sign out** ends the session on the server.
- **The top bar** holds the breadcrumbs, the **search box**, the **Jump to** button, a note on the
  state of the search index and, when the server cannot be reached, a warning that says so on
  whatever view you are on. Under "Recorded as ...: change" in the sidebar you can change the name
  your changes are recorded with.
- **Drawers.** Search and Activity open as a drawer on the left of whichever view you are on, one
  at a time.
- **The detail panel** on the right shows whichever item, requirement, decision or fact you chose,
  on any view. An epic, story or task also opens on **its own page** (see below).
- Opening a drawer, switching between the Map and the List of the Graph, changing the appearance or
  moving to another view never discards what you were typing in the detail panel or in a drawer.
- **A narrow window** (under 1100 pixels) hides the sidebar. The menu button in the top bar opens it
  over the page, and Escape, a click outside it or choosing a place closes it again.

**Appearance.** Choose Light, Dark or System with the control at the foot of the sidebar (before you
sign in it is in the top bar). The choice is kept in this browser, never with any credential, and
signing out does not clear it. System follows the operating system's setting, also while the page is
open. Changing it reloads nothing, so a filter, a selection or an unsaved edit stays as it is.

## Board

One column for each status and one lane for each epic. A card is a story or a task and shows who
holds it and until when, what it is waiting for, its due date (and whether it is overdue or planned
to start before something it needs is due), and its progress. Drag a card to another column of its
own epic to change its status, or use its **Move** menu from the keyboard. *Ready* is worked out
from the links, so nothing can be dropped there.

Above the columns, **Needs attention** counts the ready work nobody holds, the blocked and the
overdue items, the claims that ran out with no handoff note, the requirements that are in no story
and the stories that have no requirement. Each number is a link that shows exactly those items (on
a narrow window they scroll sideways in one line). The
**filter box** keeps only the cards that the [filter language](#the-filter-language) asks for.
**New item** makes an epic, a story or a task, and **Coverage** shows the requirements that are in
no story and the stories that have no requirement. Choose a card to open it in the detail panel.

## Graph

The project's work and context, as one scope shown two ways. Choose *what* to show:

- **Work**: epics, stories and tasks, with the hierarchy and the links between them.
- **Context**: requirements, decisions and facts, and the work they concern.
- **Everything**: both.

Choose *how*: the **Map**, with a different shape for each kind and a different line for each kind
of link, so colour is never the only clue, or the **List**, which says the same in text. Both show
the same records and the same relationships, and the record you chose and the epic you are looking
at stay when you switch.

Pick one epic, or all of them. A project of more than a thousand dots draws each epic as one dot and
says so. **Reach** limits the map to what is one to three steps around the chosen record. Drag a dot
to pin it, zoom and pan, colour by kind, status or epic, show replaced, withdrawn and dropped
records, show the `[[references]]` written in texts, and filter by kind, status and kind of line.
The filter box takes the work filter language. A list of the chosen record's neighbours beside the
map does everything a click on it does, from the keyboard. Choose a dot or a row to open it in the
detail panel. In the List, the title of an epic or a story opens its own page, and the small
magnifier beside it opens the detail panel instead.

## Naming items

Epics, stories and tasks are numbered separately, within a project. The board writes an item as a
letter and its number: **E-3** (epic 3), **S-12** (story 12) and **T-40** (task 40). Agents, the
address and the text of a note name it as the kind and number: `epic:3`, `story:12`, `task:40`.
Requirements, decisions and facts keep their own numbers and are written `#7` beside their kind.

## An item's own page

Choose **Open full page** in the detail panel of an epic, story or task, or the title of an epic or
story in the Graph's List, to see it as a full page (`#/item?item=story:12`).

- At the top, the item's title and where it sits, its status and priority, and how it stands: its
  progress, dates, who holds it and what it waits for. **Edit** opens the form for the title,
  details, status, priority and dates in place of the text (at the version you saw): **Save
  changes** keeps it and **Cancel** leaves it as it was. If an agent changes the item while you are
  editing, the page says so and what you typed stays.
- A list of the sections (Details, Requirements, Links, Facts and decisions, History and the table)
  stays in view as you scroll, each with how many it holds, and takes you to one.
- A task says which story, and which epic, it is part of in a card above its details, and its last
  handoff note is the first thing in its details.
- Then its details as text, with the place to move it, its requirements and its links, what
  mentions it and what the index thinks it may duplicate; across the page, the facts and decisions
  that concern it and its history.
- Under it, **a table of what is under it**: a story's tasks, or an epic's stories with their
  progress. A task has a table of its **relations** instead: the work it needs first and the work
  that needs it, with their status, priority, who holds them and when they are due. Each column's
  heading sorts the table of tasks or stories. **New task** (or **New story**) adds one, and the
  magnifier on a row opens that item in the detail panel at the right without leaving the page.

## Requirements

A requirement is what a story must meet. In the Graph's List, in the Context or Everything view,
every requirement is grouped by the story that owns it. Requirements that are in no story come
first (the newest on top), followed by the coverage report: the requirements in no story, and the
stories with no requirement, with dropped requirements and stories left out.

Write one with **New requirement**, change its title, details or status with **Edit**, and put it in
a story: drag it onto a story, or choose **Put in a story...** (**Move to another story...** for one
that is already in a story), which does it in one step. A requirement is in at most one story, and
the server refuses anything that would put it in two, in its own words. (The tools and
`[[rule:3]]` references call a requirement a rule.)

## Facts and decisions

What the project knows and what was decided, in the Graph's List, newest first.

- A **decision** is what a named person decided and why: its title, text, reason and who decided
  it. It is marked **project-wide** when it applies throughout the project.
- A **fact** is a statement with where it came from (its source) and who recorded it.

Each row says what it is, whether a person has confirmed it, and whether it is still in force.
Filter each list by status (in force by default, replaced, withdrawn or all), by whether a person
confirmed it, and by words. A list shows the first hundred and offers more.

**Record a decision** and **Record a fact** write one, and each can name the items it concerns
(picked from a list). Neither is ever edited. **Replace** records a newer one and keeps the old one
as a record, pointing at its replacement, and **Withdraw** gives one up with a reason. The form
starts from the old words, and a replaced record shows the chain. **Link** changes what a record in
force concerns. Choose a row to open it in the detail panel, where its confirmation, lifecycle,
links, mentions and history are written out.

### Confirmed or not

Every fact and decision has an **Unconfirmed / Confirmed** dropdown. A record starts
**Unconfirmed**, whoever writes it, and an agent that records something never confirms it.

- Choose **Confirmed** and the board asks who reviewed it. The name goes on the record with the
  time, and your own name is recorded separately as the one who made the change. It means that a
  named person looked at the record, not that Vrum checked it is true.
- Choosing **Unconfirmed** again asks who says so and what they said, because it takes away what a
  person gave.
- Only a record in force can change its confirmation. What a record was confirmed as stays on it,
  and in its history, after it is replaced or withdrawn.
- Replacing or withdrawing a record that a person confirmed also asks who changed their mind and
  what they said. Both the person and you are in the history.

Recall and the briefing an agent gets show every record's confirmation and whether it is in force,
so an unconfirmed record is never presented as confirmed.

## The detail panel

Choose an epic, story, task, requirement, decision or fact, from any view, a search result, the
activity feed or a `[[reference]]`, and it opens on the right.

- An **epic, story or task** shows its last handoff note, its details as text with an **Edit**
  button (the form for the title, details, status, priority and dates opens in place of the text,
  at the version you saw, with **Save changes** and **Cancel**), the place to move it, the links to
  add or remove, its requirements, the facts and decisions that concern it as an agent's `recall`
  would say them (decisions in force, project-wide decisions, facts, and the replaced or withdrawn
  ones apart, each with its confirmation and why it is there), what mentions it, what the index
  thinks it may duplicate, and its history. A requirement's details work the same way.
- A **requirement, decision or fact** shows all its fields, its confirmation and lifecycle, what it
  concerns, what mentions it and its history, with the buttons described above.

If an agent changes what you have open while you are editing it, the panel says so and offers to
load the current version, and what you typed stays.

## Roadmap

The work on a timeline, each item a bar from its start date to its target date, with a line for
today and zoom by month, quarter or year. Group by epic, status, priority or who holds it, and
filter with the [filter language](#the-filter-language).

- Drag a bar to move both dates, or one end to move that date. With a bar focused, the arrow keys
  move it a day (Shift for a week, Alt for the start only, Ctrl for the target only), and Space
  picks several bars to move together.
- A pale dashed bar is an epic or story with no dates of its own, drawn over the span of what is
  under it.
- An arrow runs from what must come first to the item that waits for it. An item planned to start
  before something it needs first is due is marked "Schedule conflict" (a warning, nothing is
  blocked), and an overdue item says so in words.
- Items with no dates wait in a tray above the timeline. It starts closed when something is on the
  timeline (press *Undated* to open it), and open when nothing is. Drag one onto the timeline to
  give it a week starting where you drop it, or type its dates there.
- *Key to the bars* explains each look: the icon and pattern of a status, a dashed bar worked out
  from what is under it, and the mark for overdue. A bar too short for its icon and words shows
  its status icon beside it instead; the row's label on the left always says what it is.
- Every move is a change at the version the bar was drawn at, so one an agent changed since is
  refused and the bar goes back, and a start after the target is refused in the server's words.
- Choose a bar to open its item in the detail panel. The roadmap has no sprints.

## Search and possible duplicates

The box in the top bar opens the search drawer, on any view, over the `search` tool. Each result
says how it matched (exact words, meaning or both) and why, shows a snippet with the matched words
marked, and its parent and status as the graph holds them now. Choose one to open it in the detail
panel, and the drawer stays open.

- Choose *Exact words only* or *Meaning only* to see one kind of match.
- The counts by kind (epic, story, task, requirement, decision, fact), by status and by epic filter
  the results and keep the server's order.
- The drawer says how many items are waiting to be indexed: results by meaning may lag behind them,
  results by exact words never do. With the embedding service stopped or not set up, search by
  words still works, and searching by meaning says why it cannot (see
  [configuration](configuration.md#search-by-meaning)).
- The search and its filters are in the address, so a search can be shared.

**Possible duplicates** is the second tab of the search drawer: pairs of stories (or epics, or
requirements) in different epics that look like the same thing, most alike first, with their scores,
side by side with their details. Change how alike they must be (the server's setting is the start),
or name epics to look at only pairs that have something in them. **Mark as duplicates** and **Mark
as related** make that link in one click, with your name. Nothing is ever linked unless you click:
looking never writes. Requirements are compared too, and cannot be linked. It needs the embedding
service.

## Markdown and references

Details, requirements, reasons, decision texts and facts are shown as Markdown by the board's own
small renderer: headings, paragraphs, lists, emphasis, code, quotes and web addresses, which open in
a new tab. Raw HTML is never rendered and nothing is loaded from elsewhere, so a script or image tag
is shown as the text it is.

Write `[[epic:3]]`, `[[story:12]]`, `[[task:40]]`, `[[rule:3]]`, `[[decision:7]]` or `[[fact:5]]` in any
text to refer to an item.
It is shown as a link with the item's current title (and follows a rename), or plainly as missing
when nothing has that number, and it opens the item in the detail panel. Typing `[[` in an editor
offers a pick-list. A reference is only text: it makes no link and changes no readiness and no
order. Every item shows **Mentioned in**, the items whose text refers to it (the `find_mentions`
tool works this out when asked and stores nothing), and the graph has a **References** switch that
draws them. Titles are not read for references.

## Jump to

Press Ctrl+K (⌘K on a Mac), or use the button in the top bar, on any view. Type a word of an item's
title, its code (`S-12`, `story 12`, `requirement 3`, `decision 7`, `fact 5`) or the name of a view,
drawer or settings page, and Enter goes there.

## Activity

The **Activity** button in the sidebar opens a drawer, on any view, with what happened, newest
first, in plain words ("Ada moved requirement 7 from story #3 to story #5"), with a handoff note shown in
full. New changes appear within seconds with no reload. Filter by who, by kind of change (work,
links, claims, handoffs, requirements, decisions, facts), by item and by day (UTC). The filter is in
the address.

Above the feed, **Who is working on what** shows every claim in force with a countdown and every
claim that ran out, and each agent's state (working, lapsed, finished). You can claim an item under
your typed name (by its number, or from the work that is free to take), renew a claim, or release it
with a note: the same operations an agent calls. A claim that ends moves to *lapsed* by itself. An
event in the feed opens its item in the detail panel.

## Settings and tools

The **Settings & tools** menu in the sidebar holds three pages.

**Agent access.** The keys of the project: make one with a label, copy it (it is shown once), see
when each was made and last used, and revoke the ones that are not wanted. It also shows the
endpoint and how to send a key. See [connecting an agent](agents.md).

**Tools.** Every tool the server has, read from the server itself so the list can never be out of
date: its description, its fields, whether it changes anything, and where on the board it can be
done, or why it is for agents only. Any tool that only reads has **Try it**, which fills in a form
from the tool's fields and shows what the server answers. A tool that changes something has no Try
it, and the server refuses one even if it is asked. The page also shows the endpoint, and how to
send an agent key with an example connection.

**Status and export.** The version of Vrum, PostgreSQL and pgvector, how many of each kind of item
there are, and the state of the search index (how many wait to be indexed, which embedding service
and model it uses, whether the service answers, the size of the vectors and the last reindex since
the server started). It is what `server_status` says, for this project. **Download export** saves
the whole graph of this project as one JSON file, identical to the one `export_graph` gives, with
nothing of any account in it. Backup, restore and import stay on the command line (see
[backup and export](backup-and-export.md)), and the page says so. No password, session or key and
not the database password is ever shown anywhere.

## The filter language

One way to say which items you mean, for the filter box of the Board, the Roadmap and the Graph's
work, and for the agents' `list_nodes` tool (as `query`), with the same answer for all of them.

- Words find items whose title or details contain them, whatever the case: `login "reset password"`.
- `field:value` narrows by a field, and commas mean "any of these": `status:ready,in_progress`. The
  fields are `kind`, `status`, `priority`, `claimed`, `parent`, `epic`, `story`, `number`, `start`,
  `target`, `is` and `no`. `epic:3` is epic 3 and everything under it, `story:12` is story 12 and
  its tasks, `parent:epic:3` is what is directly under epic 3, and `number:7` with `kind:task` is
  task 7.
- A leading `-` leaves things out: `-status:done`, `-claimed` (not held by anyone).
- `claimed` and `waiting` alone mean held by someone and waiting on something. `no:field` means
  empty (`no:start`, `no:target`, `no:claimed`, `no:parent`, `no:body`, `no:waiting`).
- `start:` and `target:` take a day, a range (`2026-11-01..2026-12-31`, either end may be left open)
  or `none`. `is:` finds `overdue`, `conflict` (planned to start too early), `waiting`, `claimed`
  and `lapsed` (a claim that ran out).
- `OR` between groups: `kind:task status:ready -claimed priority:high OR kind:story`. Terms next to
  each other must all hold. `OR` (in capitals) starts a new group.

A query that cannot be read is refused with a plain message saying what was not understood, and
nothing is listed. The filter is kept in the address, so you can share a view by copying the link.

## Addresses

Everything you choose lives in the address after the `#` (never a session or a key), so a view can
be reproduced by opening the same address, and the server keeps no saved views.

- The view is `#/board`, `#/graph` or `#/roadmap` (or `#/access`, `#/tools`, `#/status`).
- After it come the choices: `sel=story:12` (or `epic:3`, `task:40`, `rule:3`, `decision:7`,
  `fact:5`) for the record open in the detail panel; `q=` for the work filter and `epic=` (the
  epic's number) for an epic.
- An item's own page is `#/item?item=story:12`.
- For the Graph: `view=work`, `context` or `everything`, and `as=map` or `list`.
- For the Roadmap: `group=` and `zoom=`.
- For the drawers: `drawer=search` or `drawer=activity`, with `search=`, `search_mode=` and the
  other `search_...` choices, `dups=1` for possible duplicates, and `act_...` choices for the
  activity filters.

For example, `#/graph?view=context&as=list&sel=fact:5`.

## It is the same graph

A change on the board is the same operation an agent calls, with the same checks. If you drop a card
where it cannot go, or edit something an agent has changed since you read it, the board says what
the server says, in the same words, and keeps what you typed. How often the board asks for news is
`VRUM_BOARD_POLL_SECONDS` (5 by default).
