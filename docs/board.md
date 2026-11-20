# The board

The board is for people. It is at `http://127.0.0.1:8000/board`, served by the same container and
on the same port as the agents' endpoint, and it shows and changes the same graph. Whatever an
agent does appears on it within a few seconds with no reload, and whatever you do on it is what an
agent reads next. It keeps no data of its own and loads nothing from the internet.

This page describes every part of it. For a first look, see the README's
[Getting started](../README.md#getting-started).

## Accounts and projects

- **An account** is an email address and a password that Vrum keeps itself. The password is stored
  only as a salted, slow hash (scrypt), never in plain text. Several accounts may share one install.
  Vrum sends no email unless the person who runs the server sets it up (see [Email](#email)). Without
  it nothing is verified by email, and a forgotten password cannot be recovered. When the person who
  runs the server has also set up another way to sign in (OpenID Connect, GitHub or an LDAP directory),
  an account can be made by signing in that way and has no password until you set one (see
  [Signing in with another provider](#signing-in-with-another-provider)).
- **Registering** asks for a name, an email address and a password of 10 characters or more. The
  name is how the history names you, next to the agents' names (you can change it later on the
  [Users page](#users-and-identities)). Every refusal says what is wrong, and a wrong password and
  an address with no account get the same answer.
- **Signing in** gives the browser a session: a cookie the page's scripts cannot read, that expires
  after 12 hours and ends the moment you sign out. Every change from the board also carries a secret
  that only the board's own page knows and is checked against where it came from, so another
  website cannot make your browser change anything. Failed sign-ins are slowed down: five wrong
  passwords for one address stop that address for ten minutes. A sign-in by a provider gives the
  same kind of session.
- **A project** is a private workspace: its own epics, stories and tasks, acceptance criteria, notes,
  claims, history and search. After signing in you see your projects, with the role you have
  in each and a box to create the first one when you have none. Each project numbers its own items
  from 1, and each kind on its own: a project can have epic 1, story 1 and task 1, shown as E-1, S-1
  and T-1. Item S-12 in one project is not S-12 in another. Nothing is shared between projects, and
  a project can only be reached by its owner, by the people the owner invited (see
  [Members, roles and invitations](#members-roles-and-invitations)) and by the agents the owner
  gave a key to. The database itself enforces this: every row belongs to one project and cannot
  refer to a row of another.
- **A project's address** is `/board/p/<number>`. An address copied to an account that is not in
  the project opens nothing: it is refused in the same words as a project that does not exist.
- **An invitation's address** is `/board/invite/<token>` (see
  [Members, roles and invitations](#members-roles-and-invitations)). Whoever has the link can use
  it once, so it is sent only to the person it is for. When the server can send email, two more
  addresses exist, `/board/reset/<token>` and `/board/verify/<token>`: the links in the emails
  described under [Email](#email).
- **Admins and backups** stay outside accounts. `./scripts/backup.sh` and `./scripts/restore.sh`
  work on the whole database, which holds everything of everyone (see
  [backup and export](backup-and-export.md)).
- The server answers only to requests addressed to `localhost`, and compose publishes it on
  `127.0.0.1` only. That is right for one machine. Passwords and keys travel as plain text over
  HTTP: to use it from another machine, put it behind something that adds TLS (the session cookie
  is then marked Secure), and read `DECISIONS.md` (the M6 and M15 sections) first.

## The layout

A project has four views: **Board**, **Graph**, **Roadmap** and **Lists**. A project opens on the Board.

- **The sidebar** (left) holds the project switcher (with your role in the project), the four views with how much each holds,
  the epics of the project as a tree, and, at the foot, **Activity**, the **Settings and tools**
  menu and you: a person icon and your name (a link to your account on the
  Users page) with **Sign out**. The numbers beside the views are the stories and
  tasks on the Board, the work items and records in force in the Graph and in Lists, and the items
  with dates on the Roadmap. Each epic opens to its stories and each story to its tasks (only what
  you open is drawn), the item you are on is marked and its parents are open, and choosing one opens its page.
  In the tree the arrow keys move, Right opens a branch, Left closes it and Enter opens the page.
  Choosing another project in the switcher loads that project's board afresh, so nothing of the
  first can appear in the second. **Sign out** ends the session on the server.
- **The top bar** holds the breadcrumbs, the **search box** (with the key of the quick switcher
  beside its Search button: *or ⌘K*, or *or Ctrl K*, which goes away while you type), the
  **appearance control**, and, only when there is something to say, a note on the state of the
  search index (items waiting to be indexed, an index not built yet, the embedding service not
  answering, or no search by meaning set up) and, when the server cannot be reached, a warning
  that says so on whatever page you are on. An
  index with nothing waiting, and a service that answers, say nothing here: Status has the whole account. The breadcrumbs keep
  where you came from: on the page of a task opened
  from the Graph they read *Projects / your project / Graph / E-3 / S-12 / T-40*, each of the three
  by its code alone (rest the pointer on one to see its title; on the page of an acceptance criterion or a
  note the last step is its short code, *AC-3* or *N-7*), and the
  Graph stays marked in the sidebar. On the page of a record there is also a **back arrow** at the
  left of the bar: it goes exactly one step back, to the page you were on just before, as you left
  it (another record, a Roadmap with its filter, a search, a settings page), the same as the
  browser's Back button. It still works after you reload the page. If the page was opened from an
  address, with nothing before it, the arrow goes to the view the record belongs to. The view's
  step in the path always goes to that view. On a window too
  narrow for the whole path, a record's page keeps the view, the epic, the story and the record,
  and leaves out the project, which the menu shows.
- **There is no side panel and no drawer.** Every epic, story, task, acceptance criterion and note
  opens as a **page of its own** (see [Pages for every record](#pages-for-every-record)), and Search
  and Activity are pages too. A page keeps the view you came from, and the way back returns to that
  view as you left it, with its filter.
- Changing the Graph's tab, switching the kind of list, or changing the appearance never discards
  what you were typing.
- **A narrow window** (under 1100 pixels) hides the sidebar. The menu button in the top bar opens it
  over the page, and Escape, a click outside it or choosing a place closes it again. The appearance
  control stays in the top bar, as three signs (sun, moon and screen) with the same choices, and
  the search box moves to a row of its own on a phone.

**Appearance.** Choose Light, Dark or System with the control in the top bar (it is the same
control before you sign in). The choice is kept in this browser, never with any credential, and
signing out does not clear it. System follows the operating system's setting, also while the page is
open. Changing it reloads nothing, so a filter, a selection or an unsaved edit stays as it is. A
focus ring is drawn only when you use the keyboard (Tab or the arrow keys), in the text colour, and
never after a click.

**Status colours.** A status has one colour everywhere it is drawn (chips, the Board's columns, the status menus, the Roadmap's bars, the Graph's marks): New is grey, Ready is blue, In progress is violet, Blocked is red, Done is green and Dropped is grey, faded or dotted. Each also has its own sign, so colour is never the only clue. Amber is for late dates and high priority.

## Board

One column for each status and one lane for each epic. A card is a task or a story and shows who
holds it and until when, its due date (and whether it is overdue or planned
to start before something it needs is due), its **size** (a letter, *XS* to *XL*; a story that has tasks and an epic's lane show what
they add up to, *14 pts*; an open story or task with no size of its own shows a faint **No size** chip, the same ones `no:size` finds, and a story that has tasks, an epic and finished work show none), for a story or task that has acceptance criteria, how many are met (*2/3 criteria*), and, once someone has ticked a check of the
[definition of done](#definition-of-done) and not all of them, how many are ticked (*4/6 checks*, green when all are, and left out on a card that is done). A card that waits for
other work says **Waiting** (that is not the **Blocked** status, which a person sets); rest the pointer on it, tab to it, or tap it to see what it
waits for, each item with its code and title. A card whose item has a handoff note says **Handoff**. To change a status, **drag the card to another column** of its own epic; that is the only way to do it on the Board (a record's own page has a status menu too). Dropping a card into **Blocked** or **Dropped** asks for a reason first (see [Saying why](#saying-why-something-is-blocked-or-dropped)). *Ready* is
worked out from the links, so nothing can be dropped there. The priority on a card is a menu: click it to change it. A *Done* or *Dropped* card says so in a plain chip.

The small **Key** button at the end of the row of controls opens a card (rest the pointer on it, tab to it, or tap it; Escape closes it) with the six statuses and what each means, and the signs a card can carry: Waiting, Handoff, acceptance criteria met, checks ticked, size and what it adds up to, overdue, and the lock of an item that someone holds.

The **chevron** at the top right of a card folds it away to its code and its whole title (the
chips, what it is under and its notes go), and
shows it again on the next click. A folded card stays folded while you stay on the Board, whatever
the board redraws, and is open again after a reload. A card's code says what it is (E-, S- or T-),
beside a small sign for its kind.

**Show** (above the columns) chooses what the cards are, as one choice out of three:

- **Tasks**, the default: only tasks, in their columns and under their epic's lane, each saying which
  story it is under. This is the day's work.
- **Stories**: only stories, each with a progress bar and "N of M tasks done" (a story with no tasks
  says "No tasks yet" and has no bar). This is the planning view.
- **Both**: every story and task in the same columns.

The count in each column and the words of an empty lane follow the choice, and the help on
filtering explains it. The number beside Board in the sidebar describes the whole project and does
not. A filter that names a kind
(`kind:story`) decides what is shown itself, and the switch says so and keeps still. A filter that
matches things the level hides says how many, with a link to show them. The choice is in the
address, `show=tasks`, `show=stories` or `show=both` (no `show` means Tasks), and belongs to the
Board: the other views do not carry it. The Board keeps your choice while you move to other pages
in the same visit, and the address shows it when you come back.

Above the columns, the **filter box** keeps only the cards that the
[filter language](#the-filter-language) asks for; the small **i** at the end of the box shows how to
write one. **Apply** runs the filter and **Clear** (a button of its own, with a round x) empties the
box; the Roadmap and Lists have the same two buttons.
**New item** makes an epic, a story or a task, and **Coverage** opens the page that shows the
acceptance criteria that are placed nowhere and the stories that have none. Choose a card (or the
title of an epic's lane) to open its page.

## Graph

The Graph answers questions about how things are joined. It opens on **Structure**, the whole project at once, so you see what you have before you choose what to look at, and each of its three tabs (Structure, Around and Order) answers one question.

The box at the top says what the tabs are looking at, and it looks and works like the filter box on the Board, the Roadmap and Lists. The page's title, the box and **New item** sit on one line (on a narrow window the title and New item share the first line and the box has the one under them), and that line stays in view while the page scrolls. It is a search box with no filter language: type a word of an epic, story, task, acceptance criterion or note, or a code (`S-12`, `story 12`, `criterion 3`, `note 7`), and a short list of what it found hangs under the box (a code and a title, with the sign of its kind). The arrow keys move through the list, Enter chooses, Escape closes it and Tab leaves the box. Click or focus the box while it is empty and the list is the epics, so you can browse without typing. Once you choose something, the box shows it (its sign, code and title) with a small **x** that goes back to looking at the whole project. On a phone the list stays above the keyboard. Beside the box nothing is explained: it says something only when the address names an item that is not there.

What you choose is the *focus* the tabs follow, and a trail with **Back** returns to what you looked at before. With nothing chosen each tab shows what makes sense for a whole project: Structure the map of everything, Around the epics as cards (how far along each is, what is under it and how many notes are linked) and what needs attention, and Order the work of the **first epic** the project has (the lowest epic number) in phases, saying so in words, with the box at the top as the way to look at something else. One click on a card looks around that epic.

- **Structure** ("How does it all fit together?"): the whole project at once as a folder-structure map. Each epic is a hub, each story a smaller hub around it and each task a small dot around its story, joined only by their parent-child lines, so nothing crosses. Task dots carry no text: names appear as you zoom in (**Labels**: Off, On zoom or Always), and a hover card gives the code, title, status and who holds it. **Show** limits it to epics, epics and stories, or everything; **Colour by** is status or epic. **Show links between items** draws the links that cross the hierarchy (needs first, duplicate, related, replaced, named in a text), and an item chosen in the box at the top always shows its own links, with the items it joins highlighted. **Look at** (the first control) chooses the whole project or one epic alone, with every name shown. A click on a hub or a dot, or Enter on it, opens that item's page; the map always has the whole width. The **Key** button at the end of these choices opens a card (rest the pointer on it, tab to it, or tap it; Escape closes it) that draws the map's own marks: an epic with its green arc for how much is done, a story, a task, the amber ring of overdue work and the red ring of blocked work, then each colour of status with how many dots of that colour there are, and the link kinds when links are shown or an item is chosen. Every other key in the Graph is a **Key** button too.
- **Around** ("What do we know and what was decided about this?"): the focus in the middle with what is joined to it in labelled groups (part of, contains, needs first, blocks, acceptance criteria, **Notes**, similar, and the project-wide notes folded into one box). The **Notes** group lists the notes that apply, each saying its status (Unconfirmed, Confirmed, Replaced or Withdrawn); who decided a note, if someone did, is a detail on it. What is linked to the story or epic above the focus comes down to it too: those entries say "via E-1" (the item they are linked to). A task also shows the acceptance criteria of its story at one step, marked "via S-1", and a story its own; an epic's stories' and tasks' acceptance criteria appear at two steps. Replaced and withdrawn notes are struck through and unconfirmed ones are dashed. **Reach** shows one or two steps. The picture has the whole width. A click on a neighbour that is work re-centres on it, a click on an acceptance criterion or a note opens its page, and **Open page** on the item in the middle goes to its page. A button that says **Look around** wherever it appears has the same sign, five dots, and opens this tab for that item. The **Key** button beside **Reach** explains the lines.
- **Order** ("What must come first, and what does this block?"): every piece of work of one epic that is still to do, in phases. The epic is the focus's epic (with a story or task chosen, that item is highlighted in it); with nothing chosen it is the first epic the project has, and the page says so (the title names which one). A project with no epics says there is nothing to order yet. Work is the epic's tasks, and its stories that have no tasks. **Phases** is the default: phase 1 is everything that can start now (work with no link is here), and each later phase is what becomes possible when the work it waits for, in the earlier phases, is done. A done or dropped prerequisite, or one in another epic, never delays anything, and a "needs first" link between two stories counts for the tasks under them. One phase is on the page at a time, with a stepper (**< Phase 2 of 5 >**, which the arrow keys also move) and the address keeps `phase=2`. A phase has its item count and its points, and its work is grouped by story (one story is one shared context), each item with its code and title (a link to its page), status, size, due date and, after phase 1, **Waits for** the items that hold it, as buttons that go to their phase and light them. Work that is done is in a folded **Done (n)** group above the phase; dropped work is not shown, only counted ("1 dropped not shown"). With sizes, **Fit a run of up to** 5, 8, 13 or 21 points (8 by default, `budget=` in the address) cuts a phase into numbered batches ("Batch 1: 7 points, 3 items") that are filled in story order and keep a story's items together where they fit; an item bigger than the budget is a batch of its own, marked **Bigger than a run**. Batches are only a way of looking: nothing is stored. Work with no size is listed apart under **Not sized yet** and is never guessed; with no sizes at all, a phase shows only its items. **Chains** (`mode=chains`) shows the "needs first" arrows from left to right, one chain at a time with its own stepper (**< Chain 2 of 4 >**, `chain=2`), then, as the last stop, **Loose items**: the work no link names. **Highlight what is blocked** lights up what is blocked and what waits for it; resting the pointer on an item in a chain, or tabbing to it, lights what it needs first and what waits for it, and a click on it opens its page. The arrow's colour tells how the work it needs stands (grey, green when that work is done, red when it is blocked). The **Key** button beside these choices names the phases, the sizes and their points (extra small 1, small 2, medium 3, large 5, extra large 8), the statuses and what overdue means, and, for chains, a dashed box (outside the chosen epic), the red of blocked work and the highlight of the chosen chain.
The tab, the focus and each tab's choices are in the address, so a view can be shared. There is no side panel on any tab: a click on an item opens its page, where the **Agent view** shows what an agent receives for it.

## Lists

The same records as plain lists you can filter. Choose *what*: **Work** (epics, stories and tasks as an outline you can open), **Acceptance criteria** (grouped by the story or task that holds them, those placed nowhere first, with the coverage report), **Notes** (one table, see [Notes](#notes)), or **Everything**. The filter box takes the [filter language](#the-filter-language) and has its own **Apply** and **Clear** buttons; an epic box narrows to one epic, and a checkbox shows the `[[references]]` written in texts. On a window wide enough (about 1200 pixels, or about 950 where there is no sidebar) the kind, the epic, the filter box and its two buttons are on one line, the filter box giving up some length to make room (never less than about 14 rem); on a narrower one the filter takes the line under them. The title of any row opens its page. A story or task with acceptance criteria says how many are met (*2/3 criteria*) under its title in **Work**, and, once some of its checks are ticked, how many (*4/6 checks*). Each row of **Work** also has its size before its status (a letter for a task, *23 pts* for a story with tasks or an epic).

Lists start on what is current: **Notes** on the notes in force and **Acceptance criteria** on the open and met ones. Under the filters a quiet line says what that leaves out, with a button to show it (*2 replaced or withdrawn notes are not shown. Show them.*); it is not there when nothing is left out.

## Naming items

Epics, stories and tasks are numbered separately, within a project. The board writes an item as a
letter and its number: **E-3** (epic 3), **S-12** (story 12) and **T-40** (task 40). Agents, the
address and the text of a note name it as the kind and number: `epic:3`, `story:12`, `task:40`.
Acceptance criteria and notes keep their own numbers and are written `#7` beside their kind (`note:7` in an address
and in text).

## Pages for every record

Every epic, story, task, acceptance criterion and note has a page of its own
(`#/item?item=story:12`, `epic:3`, `task:40`, `rule:3`, `note:7`). It opens from a
card, a Graph row or dot, a roadmap bar, a search result, the activity feed, a `[[reference]]`, a
line of history or the quick switcher (see [Jump to](#jump-to)), and it says which view you came from (`from=` in the address).

**An epic, story or task** page has:

- At the top, its title and where it sits, its status and priority, and how it stands: progress,
  **size**, dates, who holds it (since when, and until when) and what it waits for. A second row says who
  created it and when, when it last changed and which version it is at (a refused save speaks of
  versions). **Size** is a menu on a story or task (Extra small 1 point, Small 2, Medium 3, Large 5, Extra
  large 8, and *No size*); a size not given yet shows as a dashed **Not set**, which is also the way to give
  it one. An epic has no size of its own, and neither does a story that has tasks (it adds up theirs), so
  those show **Points** instead: *23 points*, a small bar of the points that are done, and *4 not sized* when some
  of what is in it has no size yet. **Start** and **Target** are buttons: click one and
  choose a day in the calendar (see **Choosing a day** below); it is saved at once. When you hold
  the claim there is **Release...** (it asks for a note for whoever takes the work next); when
  someone else holds it the page says that only they can release it. **Edit** opens
  the form for the title, details, status, priority, size and dates in place of the text (at the version
  you saw): **Save changes** keeps it and **Cancel** leaves it as it was. If an agent changes the
  item while you are editing, the page says so and what you typed stays.
- **Agent view** (a button in the header of every record's page, also for someone who can only look)
  opens a dialog, *What an agent receives for T-5*: exactly what an agent is handed for this item
  by `recall` (the notes, the project-wide notes, the earlier notes, the acceptance criteria and
  what is alike in meaning), or by `get_note` for a note. **Close** or Escape leaves it; nothing is
  kept in the address.
- **Previous and next** buttons (also the `[` and `]` keys) with "2 of 12": the epics of the
  project, the stories of the epic, or the tasks of the story, in number order. The path in the top
  bar links up to the parents, and the table below leads down, so you can go from an epic to a
  story to a task, back, and on to the next epic without returning to a list.
- A list of the sections (Details, the table, Acceptance criteria and Definition of done for a story or
  a task, Notes, Links, History) that stays in view as you scroll, each with how many it holds
  (acceptance criteria say how many are met, *2/3*); a section with nothing in it says **empty**.
- A task says which story, and which epic, it is part of.
- Its **Details** as text; when nothing is written they are an empty field (a dashed box that says
  **Empty**) with **Add details**, which opens the editor. Then **Handoff notes**: every one the
  item ever got, newest first, each with who left it, when, how the work ended and the words in
  full, and **Handoff notes from what it needs first**: the last one of each thing it needs first,
  which is what an agent that takes the work is told. After them the place to move it, what
  mentions it and what the index thinks it may duplicate, and in the side column its links (who added
  one is in the tooltip of its row).
- **A table of what is under it**: a story's tasks, or an epic's stories with their progress. A
  task has a table of its **relations** instead: the work it needs first and the work that needs it.
  Each column's heading sorts the table, the **Size** column included (a task's size is a menu in its
  row), and a line under the rows adds the sizes up (*23 points*, *4 not sized*). **New task** (or **New story**) adds one and opens it.
- **Acceptance criteria** (a story or a task; an epic has none): a table of the ones on the item with
  how many are met (*1 of 2 met*) and **Add an acceptance criterion…**, which writes one on the item at
  once. A status chip in a row changes the criterion's status. With none the section is an empty field,
  like the details, with the same button. A task also shows what its story holds, under **From its
  story** (each a link, nothing to change there), because an agent that takes the task is told both.
- **Definition of done** (a story or a task): the project's definition of done as **checks to tick**: a
  box for each list line of the text, the line's words, and, once it is ticked, who ticked it, when, and how
  it was checked, in full. Click a box to tick it (or untick it); it is saved at once, as you, and asks
  nothing. **Add how you checked** opens a one-line field: what you write there is kept with the tick
  (and ticks it if it is not ticked), and a tick that has words has **Change how you checked**. The heading
  says *4 of 6 ticked* and the list of sections shows *4/6*. An agent ticks the same boxes, and has to
  say how it checked; an agent cannot finish a story or task as done while a box is unticked, but you can.
  A check whose line starts with **[person]** shows a **Person only** chip with a lock: only a person can
  judge it, so an agent can neither tick nor untick it (it finishes the item as blocked and says in its
  handoff note what to look at), and you tick it like any other.
  A text with no list lines has no boxes and is shown as text, as it was written. The set of checks is
  changed once, in Settings (see [Definition of done](#definition-of-done)); the section has **Edit in
  Settings** for a person who can change it, and is an empty field with a way to the page while nothing is
  written. Someone who can only look sees the boxes, and cannot change them.
- **Notes that concern it** as tables that use the whole width of the
  page: a row's words wrap to three lines, and **Show whole text** (it appears when they do not
  all fit) puts all of them in the row; a row opens that record. These are the same records, with the same reasons for
  being there, that an agent's `recall` returns: the notes in force (unconfirmed or confirmed), the
  project-wide notes, and the replaced or withdrawn ones apart (**Earlier notes**), each with its status
  and who recorded it. The project-wide and earlier sections are open when they hold fewer than five
  entries; closed, they show the first line of the first three. What is linked to the story or
  epic above an item comes down to it, and its row says where from ("From E-1 Checkout"); nothing
  comes down from an item to the one above it. **Add a note** from
  there with its button.
- **History**: everything that happened to it, newest first, with the row that made it always
  last. A change to the details or to a long title has **Show what changed**: the value before
  and after, whole and as it was written.

**An acceptance criterion or a note** page shows all its fields, its status, a
table of what it applies to (each row opens that item), what replaced it or what it replaced, what
mentions it and its history, with the buttons described below (**Edit**, **Replace**, **Withdraw**,
**Change what it applies to**, **Put on a story or task**). An acceptance criterion's page says which story
or task it is on, and an acceptance criterion's **Details** are an empty field with
**Add details** when nothing is written, as on an item's page, and its page says who created it,
when it last changed and its version. A note has one **status** (**Unconfirmed**,
**Confirmed**, **Replaced** or **Withdrawn**), shown as a chip at the top of its page. A note that
someone decided says so in a quiet line under its title (*Decided by Lena Okafor on 2026-08-25*); that
is a detail of the note, not another kind of note. A note has no
title: the first line of its text is its title in lists and links, and the page shows its text in full
and **why** it is so (where it came from, who said it, what was observed, or the reason it was
chosen). An agent that changes a
record you are editing is noticed in the same way, and what you typed stays.

**Changing a status.** A status, a priority, an acceptance criterion's status and where a note stands
are written as chips, and a chip is also a button: click it (or press
Enter, Space or the Down key on it) and a small menu opens with the choices, the current one marked.
Choose one to change it, in the Board's cards, in a page's header and tables, and in Lists.
The change is made at the version you see, so if an agent changed the item meanwhile the server
refuses it in its own words, the page shows the current version, and you choose again. Up and Down,
Home and End move through the menu, Enter chooses, a letter finds a row, and Escape closes it.

- **An epic, story or task** has the menus **Status** (New, In progress, Blocked, Done, Dropped),
  **Priority** (Urgent, High, Normal, Low) and, for a story or task, **Size** (Extra small, Small,
  Medium, Large, Extra large, or *No size*). **Ready** is worked out from what the item waits for and
  cannot be chosen: a ready item shows it as the current value, with a note saying so. Choosing **Blocked** or
  **Dropped** asks for a reason first (see below).

### Saying why something is blocked or dropped

Choosing **Blocked** or **Dropped** (from a status chip, by dropping a Board card into either column, or in the
Edit form) opens a small box, **Mark T-4 as blocked** or **Mark T-4 as dropped**, with one optional field, **Why**.
**Mark as blocked** (or **as dropped**) saves it. Leave the field empty and only the status changes; write a
reason and it is kept as a handoff note on the item, shown under **Handoff notes** on its page and in the Activity
feed (*marked T-4 as blocked and left this handoff note*), the same as an agent's. Cancel, or Escape, changes
nothing, and a card dropped into the column goes back where it was. A refusal is shown under the field with what you
typed kept. Someone who can only look is never asked.
- **An acceptance criterion** is **Open** (it stands and is still to be met), **Met** or **Dropped**. Its chip
  has boxes for signs (an empty box, a ticked box, a box with a bar), where a note's has circles, so
  the two are not mistaken for each other. Nothing sets **Met** by itself: a person or an agent does.
- **A note is never edited**, so the menu of its chip offers what can be done to a note instead.
  A note in force opens **Confirm...** (or **Un-confirm...** when it is confirmed), **Replace...**,
  **Withdraw...** and **Change what it applies to...**, each in the dialog described
  below. **Confirm** and **Un-confirm** ask who reviewed it (and, to un-confirm, what they said) exactly
  as in [Confirmed or not](#confirmed-or-not). The chip of a replaced or withdrawn note has no
  menu: its page says what replaced it, or why it was withdrawn, and the status never changes again.
- Someone who can only look at the project (a viewer) sees the same chips as plain labels. Only
  people change things on the board; an agent changes them with its own tools.

**Choosing a day.** Wherever a day is asked for, Vrum draws its own calendar, in both themes. In the
header of an epic, story or task page, **Start** and **Target** are buttons (a small calendar shows
when you point at one or tab to it): click one, choose a day, and it is saved at once, at the version
you see, as a status is. **Clear** takes the date away and **Today** picks today's day (the UTC day,
the one the Roadmap and the overdue count use). The server still decides: a start after the target,
or a target before the start, is refused in its own words and the date stays as it was. In a form
(**Edit**, **New item**, a note's **Decided on**, and the days of the Activity filter) a day is a text
field you can type into as `2026-11-30`, with a calendar button at its end that fills it in; nothing is
checked until the form is sent. The calendar opens under its button (above it when there is no room)
and has Previous and Next month buttons. With the keyboard, the arrow keys move a day or a week,
PageUp and PageDown a month (with Shift, a year), Home and End go to the start and end of the week,
Enter picks, and Escape closes the calendar and nothing else, so a dialog around it stays open. The
week starts on Monday, as on the Roadmap. A viewer sees the dates as plain text.

**Coverage** (`#/coverage`) is the report of the acceptance criteria that are placed nowhere and the
stories that have none (on the story or on any of its tasks), each as a table, with **Put on a story
or task** beside every acceptance criterion and **Add an acceptance criterion…** beside every story.

## Acceptance criteria

An acceptance criterion is what a story or a task must meet. It is **Open** (it stands and is still to
be met), **Met** or **Dropped**, and it is on one story or one task, or on none yet (an epic cannot
hold one). In Lists, under **Acceptance criteria**, every one is grouped by the story or task it is on.
Those placed nowhere come first (the newest on top), followed by the stories with no acceptance
criterion (dropped ones and dropped stories left out). The **Status** filter offers **Open and met**
(the default), **Open**, **Met**, **Dropped** and **All**; when the default leaves some out, a line says
how many with a button to show them. To find the work that has none of its own, filter the Board,
the Roadmap or Lists with `no:criteria`, and with `no:body` for work with no details.

Write one with **Add an acceptance criterion…**, in this list or on the page of a story
or a task, which puts it there at once. Change its title, details or status with **Edit**. Put it on
a story or task by dragging it onto one, or with **Put on a story or task...** (**Move to another story
or task...** for one that is already on one): a box finds a story or a task by its code or a word of its
title, in a list grouped by epic, and the move is one step. (Putting one on a story or task and moving it
to another are one operation, `place_rule`, for the board and for an agent.) An acceptance criterion is
on at most one item, and the server refuses anything that would put it on two, in its own words. (The
tools and `[[rule:3]]` references call an acceptance criterion a rule.)

## Definition of done

The definition of done is one text for the whole project: what every story or task has to satisfy on top
of its own acceptance criteria (tests pass, a person can try it, the docs say so, and so on). Its **list
lines are its checks**: write one line for each, starting with a dash, and every story and task gets a
box for each to tick, by a person or an agent (see the page of an item above); any other line is context and
is shown as it was written. A line that starts with **[person]** (`- [person] Looks right on a phone`) is a check
only a person can judge: it shows a **Person only** chip, a person ticks it like any other, and an agent is
refused if it tries; the tag is not part of the check's words, so adding or removing it keeps every tick. The page's **Key** button
shows the syntax. It is under
**Settings and tools**, **Definition of done** (`#/definition`), with **Edit** for an
owner or an editor (**Save changes** keeps it, **Cancel** leaves it) and an empty field with **Add a
definition of done** while nothing is written. The page says how many checks the text holds (*6 checks*,
counted as you type) and, for each, on how many stories and tasks it is ticked (*ticked on 12 stories
and tasks*). Rewording or taking out a line makes it a different check, and the ticks the old one had
stop counting: while you edit, the page says how many ticks that costs. The page says who changed the text last and when. Only a
person changes it: it is saved at the version you saw, so a change made meanwhile is refused in the
server's words and what you typed stays. It is shown on the page of every story and task, and an agent
that takes a task is told it and its checks. Changing it is in the Activity feed (*changed the definition of done*, with
**Show what changed**).

## Notes

What the project knows and what was decided is one kind of thing: a **note**. It is in Lists under
**Notes**, newest first, as one table with the columns **Note**, **What it says**, **Status**,
**Applies to**, **Decided by**, **Recorded by** and **Recorded**.

- A note is a **text** (its first line is its title in lists and links) and **why** it is so: where it
  came from, who said it, what was observed, or the reason it was chosen. It has no title of its own.
- A note has **one status**: **Unconfirmed** (no person has reviewed it yet), **Confirmed** (a named person
  has), **Replaced** (a newer note took its place) or **Withdrawn** (it should not be relied on). The
  first two are the notes *in force*; the last two are kept as history. Each status has a sign and a
  word, never a colour alone.
- A note may name **who decided it and when**. That is a quiet detail, shown in the **Decided by**
  column and under the title of its page; it does not make it another kind of note. The person who
  decided is not the agent or person who recorded it (the record says who recorded it separately).
- A note is marked **project-wide** when it applies throughout the project, whether or not someone
  decided it.

Each row shows where the note stands in its status chip and what it applies to. The **Status** filter
offers **All**, **In force** (unconfirmed and confirmed together, the default), **Unconfirmed**,
**Confirmed**, **Replaced** and **Withdrawn**, and the box beside it finds words. A list shows the
first hundred and offers more.

**Add a note…** (in Lists, and on the page of any epic, story, task, acceptance criterion or note) asks for the
text, **why**, two optional fields, **Decided by (optional)** and **Decided on** (name the person who
decided it, and leave the day empty for today), **Applies throughout the project** and the
items it applies to (picked from a list). No field is marked as required: a blank is sent and the server's
refusal is what you read. A note is never edited. **Replace** records a newer note and keeps the old one as a record,
pointing at its replacement (the new note's **why** says why it was replaced, and the old note's page
shows it), and **Withdraw** gives one up with a reason. The form starts from the old words, and a
replaced note shows the chain. **Change what it applies to** changes what a note in force applies to
(items, acceptance criteria and other notes it is about). Choose a row to open its page, where its
status, links, mentions and history are written out.

### Confirmed or not

A note starts **Unconfirmed**, whoever writes it, and an agent that records something never confirms
it. Its status chip (in the header of its page and in every row of a list or table) opens a menu; this
is how a person confirms it.

- Choose **Confirm...** and the board asks who reviewed it, with your own name filled in (change it
  if someone else looked at the note). The name goes on the note with the
  time, and your own name is recorded separately as the one who made the change. It means that a
  named person looked at the note, not that Vrum checked it is true. Only a person confirms: an
  agent is refused if it tries. The chip then says **Confirmed**; rest the pointer on it to see
  who and when. The note's page shows the same in **Confirmation**, with what the person said
  (the confirm box asks for it) in full, and the history shows it too.
- Choosing **Un-confirm...** on a confirmed note asks who says so (your name is filled in here too) and
  what they said, because it takes away what a person gave. The page then says "Confirmation was
  taken back by" that person, with their words.
- Only a note in force can be confirmed or un-confirmed. When a note is replaced or withdrawn, the
  page still says who had confirmed it, and it stays in its history.
- Replacing or withdrawing a note that a person confirmed also asks who changed their mind and
  what they said. Both the person and you are in the history.

Recall and the briefing an agent gets show every note's status, so an unconfirmed note is never
presented as confirmed.

## Roadmap

The work on a timeline, each item a bar from its start day to its target day, with a ruler along
the top, a column for today and zoom by month, quarter or year. It opens on Month, about five
weeks wide on a wide window, with today a third of the way in, so that the bars are big enough to
read; Quarter shows about a quarter and Year about a year, and the pan buttons and **Today** move
along it. Group by epic, status, priority or who holds it, and filter with the
[filter language](#the-filter-language).

- **A ruler and a grid you can count on.** On Month the ruler has a cell for every day with its
  number (today's is a pill, weekends a little darker) under the name of the month, which stays
  beside the labels while you scroll through it. On Quarter it has a tick for every day, taller on
  Mondays and the first of a month, with the number on Mondays; on Year a tick for every week and
  every month. Behind the rows runs a very faint grid: on Month a line at every day and every row,
  so a cell is one day wide and one item high (a week wide on Quarter, a month on Year). A bar
  covers its start day through its target day, cell for cell, so a one-day task fills one cell, and
  the bar's hover text says it: "5 days: 3 Dec to 7 Dec" (and its size, when it has one).
- **Late work shows how late.** An item that is past its target date and not done draws a hatched
  tail from the end of its bar through today's cell, a cell for each day it is late, with "3 days
  late" beside it. Drag its end to a later date and the tail gets shorter; to a day that has not
  passed and it goes.
- Drag a bar to move both dates, or one end to move that date. With a bar focused, the arrow keys
  move it a day (Shift for a week, Alt for the start only, Ctrl for the target only), and Space
  picks several bars to move together. Shift-click picks bars too.
- A pale dashed bar is an epic or story with no dates of its own, drawn over the span of what is
  under it.
- An arrow runs from what must come first to the item that waits for it. An item planned to start
  before something it needs first is due is marked "Schedule conflict" (a warning, nothing is
  blocked), and its arrow is dashed and amber ("planned too early").
- Only work with dates is drawn here (its own, or worked out from what is under it). The line
  under the controls says how many items are drawn and how many are not because they have no
  dates ("2 items on the timeline. 81 have no dates and are not drawn: see them in Lists"); that
  last part opens Lists on the filter `no:start no:target`. To
  put an item on the timeline, give it a start or target date on its page.
- The **Key** button beside the summary opens a card (rest the pointer on it, tab to it, or tap
  it; Escape closes it) that says what every mark means, each drawn with the same look as on the
  timeline: the colour, pattern and icon of each status; the day cell, the weekend and the late
  tail; a pale dashed bar worked out from what is under it; the amber outline of a schedule
  conflict (also marked at the end of the item's row); the status icon that stands beside a bar
  too short for its words; a bar chosen to move with others and the ends you drag, and the keys
  above (only if you may change things); the line for today and the two kinds of arrow; and the
  rows of an epic, a story and a task.
- Every move is a change at the version the bar was drawn at, so one an agent changed since is
  refused and the bar goes back, and a start after the target is refused in the server's words.
- Choose a bar (or a row's label) to open its page. The roadmap has no sprints.

## Search and possible duplicates

The box in the top bar opens the **Search page** (`#/search`), over the `search` tool. A search
always uses exact words and meaning together. Each result
says how it matched (exact words, meaning or both) and why, shows a snippet with the matched words
marked, and its parent and status as the graph holds them now, in a table. Choose one to open its
page.

- The counts by kind (epic, story, task, acceptance criterion, note), by status and by epic filter
  the results and keep the server's order. A note's row says where it stands (Unconfirmed, Confirmed,
  Replaced or Withdrawn) and an acceptance criterion's says Open, Met or Dropped; the status counts use the same words.
- When items are waiting to be indexed the page says how many: results by meaning may lag behind
  them, results by exact words never do. With none waiting it says nothing. With the embedding
  service stopped or left out, search by
  words still works, and the page says in the server's words why meaning was not used (see
  [configuration](configuration.md#search-by-meaning)). When meaning works there is no note, since
  each result says how it matched.
- The search and its filters are in the address, so a search can be shared.

**Possible duplicates** is the second tab of the Search page: pairs of stories (or epics, or
acceptance criteria) in different epics that look like the same thing, most alike first, with their scores,
side by side with their details. Change how alike they must be (the server's setting is the start),
or name epics to look at only pairs that have something in them. Each button on a pair says which two
items it links: **Mark S-24 and S-50 as duplicates** and **Mark S-24 and S-50 as related** make that
link in one click, with your name, and the button then reads *S-24 and S-50 are marked as duplicates*.
Nothing is ever linked unless you click:
looking never writes. Acceptance criteria are compared too, and cannot be linked. It needs the embedding
service. The page says nothing about meaning while it works as it should; it speaks only when meaning
could not be used or items are still waiting to be indexed.

## Markdown and references

Details, acceptance criteria, notes, the definition of done and their reasons are shown as Markdown by the board's own
small renderer: headings, paragraphs, lists, emphasis, code, quotes and web addresses, which open in
a new tab. Raw HTML is never rendered and nothing is loaded from elsewhere, so a script or image tag
is shown as the text it is.

Write `[[epic:3]]`, `[[story:12]]`, `[[task:40]]`, `[[rule:3]]` or `[[note:7]]` in any
text to refer to an item.
It is shown as a link with the item's current title (and follows a rename), or plainly as missing
when nothing has that number, and it opens the item's page. Typing `[[` in an editor
offers a pick-list. A reference is only text: it makes no link and changes no readiness and no
order. Every item shows **Mentioned in**, the items whose text refers to it (the `find_mentions`
tool works this out when asked and stores nothing), and Structure's **Show links between items**
draws them with the other links. Titles are not read for references.

## Jump to

Press Ctrl+K (⌘K on a Mac) on any view; the search box in the top bar says so beside its Search
button (*or ⌘K*). Type a word of an item's
title, its code (`S-12`, `story 12`, `criterion 3`, `note 7`) or the name of a view,
page or settings page, and Enter goes there.

## Activity

The **Activity** link in the sidebar opens the Activity page (`#/activity`), with what happened, newest
first, in plain words ("Ada moved acceptance criterion 7 from story S-3 to story S-5"), with a handoff note shown in
full. New changes appear within seconds with no reload. Filter by who, by kind of change (work,
links, claims, handoffs, acceptance criteria, the definition of done, checks, notes), by item and by day (UTC). A size change reads
"sized task T-3 as Medium", "changed the size of task T-3 from Small to Large" or "took the size off task T-3", and a tick
"ticked “Tests pass” on task T-12: 212 tests pass" (or "unticked"), with **Show what changed** when what was said of how it was
checked is long. The filter is in
the address.

Beside the feed, **Who is working on what** shows every claim in force with a countdown and every
claim that ran out, and each person's or agent's state (working, lapsed, finished). You claim an
item as yourself (by its number, or from the work that is free to take), and you can renew or
release the claims you hold, with a note: the same operations an agent calls. A claim belongs to
whoever took it, a person or an agent, and only that one can renew or release it, so the other
claims have no buttons (the page of the item says who holds it, and that only they can release it).
**Free to take** shows the first ten and **Show more** reads ten more each time. A claim that ends moves to *lapsed* by itself. An
event in the feed opens its item's page. A change to details or to a long title has **Show what
changed**, with the value before and after in full; a claim that was released with a note reads
"handed the work back".

## Users and identities

Every change, claim and record is kept under who made it: a name, and whether it is a **person**
or an **agent**. The server decides that from the credential and never from anything typed. A
change made on the board is the signed-in person's; a call to the MCP endpoint is the agent whose
key it carries (a revoked key stops at once). That is why the board has no box to type a name in
and no tool takes an `actor` argument.

Wherever the board says who did something or who holds something (the history of an item and the
Activity feed, who holds a task on a card, a row or a page, the claims and each person or agent in
Who is working on what, who recorded a note, a handoff note) the name has a small sign before it:
a person icon, or a robot head for an agent, and a screen reader reads "Lena Okafor, a person" or
"api-agent, an agent". History imported from a project exported before identities existed has no
kind, and shows the name alone. A name someone *types* about another person (who decided, who
reviewed and confirmed, whose words are quoted) stays plain text, since it names people who may
have no account. The board fills it in with your name, and you can change it.

The **Users** page (`#/users`, in the Settings and tools menu, and the link at the foot of the
sidebar) has three sections.

- **Account.** Your name (change it and choose Save: it is how the history names you from then on,
  and what was recorded earlier keeps the name it was recorded under), your email (it cannot be
  changed yet; when the server can send email it also says **Verified** or **Not verified yet**,
  with a **Send verification email** button until it is verified, see [Email](#email)) and **Change
  password** (the current one, the new one and the new one again: your other sessions end). **Sign
  out** is at the foot of the sidebar. When the server offers another way to sign in, or your account has no password,
  Password becomes **Sign-in methods**: see
  [Connecting and disconnecting](#connecting-and-disconnecting).
- **Members.** The people of the project, each with their role. Everyone in the project sees the
  list, and the owner also sees their email addresses. The owner can **Invite someone**, change a
  member's role and remove a member; anyone who is not the owner can **Leave this project**. See
  [Members, roles and invitations](#members-roles-and-invitations).
- **Agents.** The project's agent identities, one for each key: its name, the start of the key,
  when it was created and last used, whether it is active, **what it may do** in words, **Change
  what it may do…** and **Revoke**. **New agent key** asks for the agent's **name**, which is what
  the history shows for everything that uses the key, so make one key for each agent; **Create
  key** makes it. A name is 1
  to 100 characters and unique among the project's active keys. The key itself is shown once, with
  a **Copy** button, and with what it may do: only a hash is kept, so a lost key is revoked and
  replaced. **Connect an agent** gives the endpoint (with Copy) and the header to send a key in
  (see [connecting an agent](agents.md)). Only the owner creates, changes and
  revokes keys: an editor or a viewer is told so and sees only how an agent connects.

  **What may this agent do?** is the second step of making a key. A client can take only so many
  tools from one server (Cursor, for one, allows 40 across all its servers), so an agent is offered
  a chosen set, the **standard tools**: 25 today, for the work loop, items, links, acceptance criteria,
  notes, recall and search. The rest, such as histories, moving data and confirming a note, are
  done on the board and are not offered to agents by default. A new key starts on **Standard**,
  which gives it no list of its own: it gets exactly the standard tools, and a tool Vrum offers
  agents later is included. Five quick choices tick the matching boxes: **Standard**, **Read
  only** (the offered tools that only read), **Read and work** (those, and the offered tools that
  change epics, stories and tasks, links and claims), **Read, work and record** (those, and the
  offered tools that write acceptance criteria and notes; when that is the same set as Standard, as it
  is today, it is not shown twice) and **Everything, including administration** (every tool the
  server has today except the ones only people can use, including moving data and rebuilding the
  index, for a key that a backup script uses; a tool added later is not included).
  **Choose tools one by one** opens the full list. The tools Vrum offers agents come first, in the
  areas the Tools page groups them by: each area has a box that ticks or clears all of its tools
  (half ticked when only some are), each tool shows its name, one line about it and whether it
  only reads or changes something, and a search box narrows the list. Below them is a closed
  section, **Not offered to agents by default**, with the other tools, each with its area, none
  of them ticked. Ticking a tool by hand moves the choice to **Custom**. A tool only people can
  use (confirming a note) is shown but cannot be ticked. A line says how many tools are allowed:
  *25 tools (standard)*, *All 47 tools* or *12 of 47 tools*. Ticking nothing is refused: *Tick at
  least one tool, or leave the key with the standard tools.*

  A key that lists its tools may use exactly that list, and it may name tools outside the
  standard set, so a tool such as `export_graph` or `node_history` works for that key and for no
  other. A tool added to Vrum later is not allowed to a key that lists its tools until someone
  ticks it; **Standard** follows the tools Vrum offers.

  In the key list each key says what it may do: *Standard, 25 tools*, *Read only, 9 tools*, *Read
  and work, 18 tools*, *Everything, 47 tools* or *Custom, 12 tools*. The owner can **Change what
  it may do…** on an active key, which opens the same list in a dialog (the section of tools not
  offered is open when the key already has one of them); what is saved applies from the agent's
  very next request, even on a connection that is open now. An agent sees only the tools it may
  use, and a call to any other is refused in plain words with nothing changed. A key with no list
  of its own is told that the tool is not one of the tools Vrum offers agents, so the key (named)
  cannot use it: it is done on the board, by a person, and the project's owner can allow it for
  this key on the Users page. A key with a list is told that it is not allowed to use the tool
  and to ask the owner to change what the key may do. A revoked key keeps showing what it could
  do and cannot be changed.

## Members, roles and invitations

A person is in a project with one of three roles, and the server checks the role on every request:

- **Owner.** Everything: the work and what is known about it, and also who is in the project and
  its agent keys. The person who made the project is its owner, and there is one.
- **Editor.** Can read and change everything in the project, but not who is in it or its agent
  keys. An editor confirms and un-confirms notes as a person.
- **Viewer.** Can look at everything and change nothing.

The role is shown beside each project in your list of projects and in the project switcher in the
sidebar (small muted text, *Owner*, *Editor* or *Viewer*). A viewer sees a **View only** chip in
the top bar, whose tooltip says they can look but not change, and the controls that write are
simply not on their pages: no New item, New story, Add a note, Edit, Move,
acceptance criterion actions, claim, renew or release, the buttons that mark a pair as duplicates or related, status menus or drag
handle. Anything that is still asked is refused by the server in plain words, and the board shows
that as a notice like any other.

**Inviting someone.** On the Users page the owner chooses **Invite someone**, picks *Editor* or
*Viewer* (each comes with one line on what it can do) and gets a link, shown once with a **Copy**
button: *Send this link to the person. It works once and for 7 days. Vrum does not send email.* (With
email set up the dialog can also email it: see [Email](#email).)
Only a hash of the link is kept, so a lost link is withdrawn and replaced. The invitations still
waiting are listed under the members, with their role, when they were made, by whom and when they
run out, and each can be withdrawn.

**The invitation page** (`/board/invite/<token>`). Someone who opens the link and is not signed in
sees the sign-in and create-account form with a line saying why, and comes back to the invitation
after signing in or registering. A signed-in person sees what they would join (the project, its
owner, the role and what it allows, who invited them and until when) and **Join <project> as an
editor** (or *a viewer*); joining opens the project's board. A link that is used, expired,
withdrawn or never was gets one plain answer, with a way back to the projects. A person who owns
the project or is in it already is told so, and one whose name someone in the project already uses
is asked for another name on the same page, since the history tells people apart by their names.

**Changing who is in the project.** The owner changes a member's role with the dropdown beside
their name (it takes effect at once, on the person's next request) and removes a member with
**Remove**, after a confirmation. What a removed or departed person did stays in the history under
their name. A member who is removed while the board is open is told that the project is not theirs
any more.

## Email

Email is **optional and off by default**. Vrum sends none until the person who runs the server sets
it up (see [configuration](configuration.md)), and nothing on the board changes until then: no
**Forgot your password?**, no email address when inviting, and invitations are shared by copying
the link. Every message is plain text with one link in it, and a message that cannot be sent never
stops what you were doing: the board says what went wrong and the action still happens.

**Choosing a new password.** When the server can send email, the sign-in page has **Forgot your
password?**. Type your email address and choose **Send the link**. The page answers in the same
words whether or not that address has an account (*If that address has an account, a link to choose
a new password has been sent to it. The link works once for one hour.*), so it cannot be used to
find out who has one. The link opens `/board/reset/<token>`, which checks the link, asks for a new
password twice and says *Your password is changed. Sign in with the new one.* Setting it ends every
session of the account, so everyone signed in with the old password has to sign in again, and it
also confirms the address, since the link reached its mailbox. Agent keys are not touched. A link
that was used, has run out (one hour) or never was is refused in one plain sentence, with **Ask for
a new link**. Opening the page never uses the link, and the board takes the link's token out of the
address bar as soon as it has read it.

**Confirming an address.** Registering sends a message with a link, and the **Email** setting on the
Users page shows **Verified** or **Not verified yet**, with **Send verification email** while it is
not (a few a day at most; the answer says what was sent and the next press says **Send it again**).
The link opens `/board/verify/<token>`, which confirms the address and says so, and works once for
24 hours. Confirming changes nothing you can do: an account that is not confirmed works fully. It
is only recorded and shown.

**Inviting by email.** The **Invite someone** dialog gains **Email address (optional)**: *Vrum will
email the link to this address. Only that address can accept it. You can still copy the link.* With
an address the button says **Send the invitation**. The result shows *Emailed to <address>* (or why
it was not sent, in plain words, with the invitation made all the same) and the link, shown once as
before. A signed-in person who opens it with a different account is told which address it is for,
in part (`p***@example.com`), and can sign in with another account; an account that registers from
the link with that address counts as confirmed. Invitations with an address say *Sent to <address>*
in the waiting list, and **Resend** makes a new link, emails it and ends the old one (a few an hour
at most).

## Signing in with another provider

Signing in with OpenID Connect, GitHub or an LDAP directory is **optional and off by default**. The
person who runs the server sets up any of the three in the server's settings (see
[configuration](configuration.md)). Until one is set up, the board has no button, page or setting
for any of them, and the sign-in page is the one described under
[Accounts and projects](#accounts-and-projects).

- **OpenID Connect** is for an organization's identity service (or any service that speaks OpenID
  Connect). The button is named by whoever runs the server, so it can say *Sign in with Acme Login*.
- **GitHub** signs in with a GitHub account (github.com, or a GitHub Enterprise server).
- **LDAP** signs in with a username and password from a company directory.

**Signing in.** Under the sign-in form there is a quiet *or* and one button for each way the server
offers: **Sign in with <name>** (the create-account page says **Continue with <name>**).

- *OpenID Connect and GitHub.* The button takes you to the provider's own page. You sign in there,
  so Vrum never sees your password for it, and the provider sends you back to Vrum, signed in, with
  the same session a password sign-in gives. You land on the page you were opening (the project
  list, or a project's page), or on the invitation if you came by an invitation's link.
- *LDAP.* The button opens a **Username** and **Password** form right under it, with one line: *Your
  password goes to Vrum, which checks it with the directory. Vrum does not keep it.* A name that
  does not exist and a wrong password get the same answer, *The username or password is not right.*
  Wrong tries are counted and slow down like the ones for a password. Escape puts the form away.

If the server does not make accounts from a provider's sign-in, the create-account page shows none
of these buttons, and the sign-in page keeps them for people who already have an account.

**Which account you are.** The first time you sign in with a provider, Vrum has to work out who you
are, and it never joins a provider to an account it cannot be sure is yours.

1. If that provider account is already connected to a Vrum account, you are signed in to it. Vrum
   does not look at the email address again, so a changed address changes nothing.
2. Otherwise the provider has to give a **confirmed** email address: OpenID Connect must say the
   address is verified, GitHub must have a verified primary address, and a directory's address is
   trusted because whoever runs the server owns the directory. The server can also limit which email
   domains may sign in this way.
3. If a Vrum account with that address exists and **its address is confirmed** (by the link in an
   email, an emailed invitation or a password reset, see [Email](#email)), the provider is
   connected to it and you are signed in.
4. If a Vrum account with that address exists but its address was **never confirmed**, Vrum refuses
   and changes nothing. Someone could have registered your address with a password of their own, and
   joining it to your real login would give them your account. Sign in with that account's
   password, then connect the provider from the Users page.
5. If there is no account with that address, Vrum makes one (unless the server is set not to): its
   name is the one the provider gives for you, or the part of your address before the @ sign, the
   address counts as confirmed, and it has no password.

**An account with no password.** An account made by a provider has no password, so email and
password cannot sign in to it until you set one. The sign-in page gives the same answer as for a
wrong password. Users page, Sign-in methods, **Set a password…** sets a first one (it asks only for
the new password, twice), and **Forgot your password?** does the same when the server can send
email.

### Connecting and disconnecting

The Users page, in Account, shows **Sign-in methods** (instead of **Password**) when the server
offers another way to sign in or your account has no password. It lists the password and each way
the server offers, one line each:

- **Password.** *You can sign in with your email and password*, with **Change password…**. With no
  password it says *You have no password. Sign in with <names>. Set a password to also sign in with
  email.*, with **Set a password…**.
- **A provider that is connected** says *Connected as <the provider's name for you>*, with the
  provider's address for you and when it was connected, and **Disconnect…**. Disconnecting asks to
  confirm and changes nothing else: your account, your projects and everything you recorded stay.
  It also ends your other sessions; the one you are in stays.
- **A provider that is not connected** says *Not connected* and has **Connect <name>**. OpenID
  Connect and GitHub take you to the provider and back to the Users page, where a notice says the
  provider is connected. A directory asks for its username and password in a dialog, which Vrum
  checks and does not keep.

Vrum does not let you remove the last way you can sign in: *That is the only way you can sign in.
Set a password or connect another provider first. Nothing was changed.* One provider account can be
connected to only one Vrum account, and one Vrum account has at most one connection for each
provider.

### When a sign-in does not work

When you come back from a provider and it did not work, the sign-in page shows one sentence above
the form. Nothing was changed, you have no session, and the address is cleaned at once so a reload
does not say it again. A problem while connecting a provider to an account you are signed in with
shows as a notice on the Users page. `<name>` below is the provider's name on its button.

| The page says | What it means |
| --- | --- |
| *Signing in with <name> was cancelled. Nothing was changed.* | You, or the provider, stopped the sign-in. Start again if you want to. |
| *Signing in with <name> did not work. Nothing was changed. Try again, and if it keeps happening ask whoever runs this server.* | The provider's answer could not be trusted or used (for example it was not signed properly). The words never include what the provider said, so nothing from it can reach the page. The server's log holds the reason without any secret. |
| *That sign-in took too long or was already used. Start again.* | A sign-in is good for ten minutes and once, in the browser that started it. Reloading the way back, opening it in another browser or restarting the server ends it. |
| *Vrum could not reach <name>. Try again in a moment.* | The server could not talk to the provider. |
| *<name> did not give a confirmed email address, so Vrum cannot tell who you are. Confirm your email address there, or sign in another way.* | The provider gave no address, or did not say it is confirmed. |
| *Accounts with that email address may not sign in this way on this server.* | The server only lets some email domains use this way in. |
| *An account with your email address already exists here, but its address was never confirmed, so Vrum will not join the two. Sign in with its password, then connect <name> from the Users page.* | See step 4 above. |
| *This server does not make new accounts from <name> sign-in. Sign in with an account you already have, or ask whoever runs it.* | Only people who already have an account may use this way in. |
| *That <name> account is already connected to another Vrum account.* | Connecting: that provider account belongs to someone else's (or your other) Vrum account. |
| *<name> is already connected to this account. Disconnect it first.* | Connecting: this account already has a connection for that provider. The same words are also given when a first sign-in has the address of a confirmed account that already has a different <name> account connected, since joining the two by address would swap one person for another. |
| *You were signed out while connecting <name>. Sign in and try again.* | Connecting: your session ended on the way. |

A problem code the board does not know shows the second sentence above, so nothing the address
holds is ever shown as it is. What a provider says about you (your name, your address) appears only
as text.

**What Vrum keeps.** For each connection: which provider, the id the provider gives you (not a user
name, which can change), the address and the name it gave, and when it was connected. Never a
provider's token, a code or your directory password. A sign-in in progress is kept in the server's
memory only, so restarting the server ends the ones on their way.

## Settings and tools

The **Settings and tools** menu in the sidebar holds four pages.

**Users.** Who is in the project and how each shows in its history: your account, the members with
their roles and the invitations, and the agents with their keys (see
[Users and identities](#users-and-identities)). An old address,
`#/access`, is no longer a page and opens the Board.

**Definition of done.** The project's one definition of done (see [Definition of done](#definition-of-done)).

**Tools.** One page for the one MCP server: first **Connect an agent** (the endpoint, with a Copy
button, the header an agent key is sent in, and **Create an agent key**, a link to the Users page),
then every tool the server has, read from the server itself so the list can
never be out of date. A switch, **All tools**,
**Offered to agents** and **Board and administration only** (each with its count), lists either
kind alone; it is kept in the address (`show=offered` or `show=board`) and works with the search,
which also says how many tools it shows.
The tools are grouped only to make them easier to find, and inside each group the ones that only
read come first and the ones that change something after. For each tool: its description, its
fields, whether it changes anything, a quiet chip that says **Offered to agents** or **Board and
administration only**, and where on the board it can be done, or that it has no page there and
why. A tool only people can use says so. Any tool that only reads has **Try it**, which fills in a
form from the tool's fields; **Run** shows what the server answers (it is the board that asks, as the
person signed in, so Try it works for a tool that is not offered to agents too). A tool that
changes something has no Try it, and the server refuses one even if it is asked.

**Status and export.** The version of Vrum, PostgreSQL and pgvector, how many of each kind of item
there are, and the state of the search index (how many wait to be indexed, which embedding service
and model it uses, whether the service answers, the size of the vectors and the last reindex since
the server started), and whether the server can send email: *Set up: sending as <name and address>
through <host>*, or *Not set up: invitations are shared by link and passwords cannot be reset by
email* (never the mail server's user name or password). It is what `server_status` says, for this
project, and the email line. When the server offers another way to sign in, a **Sign-in** section
lists each one (its button's name, what kind it is and the address it talks to, never a client
secret or a password), which email domains may use it, and whether a first sign-in may make a new
account. **Download export** saves
the whole graph of this project as one JSON file, identical to the one `export_graph` gives, with
nothing of any account in it. Backup, restore and import stay on the command line (see
[backup and export](backup-and-export.md)), and the page says so. No password, session or key and
not the database password is ever shown anywhere.

## The filter language

One way to say which items you mean, for the filter box of the Board, the Roadmap and Lists,
and for the agents' `list_nodes` tool (as `query`), with the same answer for all of them. The small
**i** at the end of each filter box shows this page's forms as a card, with an example of each
(rest the pointer on it, tab to it, or tap it).

- Words find items whose title or details contain them, whatever the case: `login "reset password"`.
- `field:value` narrows by a field, and commas mean "any of these": `status:ready,in_progress`. The
  fields are `kind`, `status`, `priority`, `size`, `claimed`, `parent`, `epic`, `story`, `number`, `start`,
  `target`, `is` and `no`. `size:m,l` finds stories and tasks of those sizes (`xs`, `s`, `m`, `l`, `xl`). `epic:3` is epic 3 and everything under it, `story:12` is story 12 and
  its tasks, `parent:epic:3` is what is directly under epic 3, and `number:7` with `kind:task` is
  task 7.
- A leading `-` leaves things out: `-status:done`, `-claimed` (not held by anyone).
- `claimed` and `waiting` alone mean held by someone and waiting on something. `no:field` means
  empty (`no:start`, `no:target`, `no:claimed`, `no:parent`, `no:body` for no details, `no:criteria` for no acceptance criteria of its own, `no:size` for a story or task with no size of its own, except a story that has tasks, `no:waiting`).
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

- The page is `#/board`, `#/graph`, `#/roadmap`, `#/lists`, `#/search`, `#/activity` or `#/coverage` (or
  `#/users`, `#/definition`, `#/tools`, `#/status`).
- After it come the choices: `q=` for the work filter and `epic=` (the epic's number) for an epic.
  The Board also has `show=` (`tasks`, `stories` or `both`; no `show` is `tasks`).
- A record's own page is `#/item?item=story:12` (or `epic:3`, `task:40`, `rule:3`, `note:7`),
  with `from=board` (or `graph`, `roadmap`, `lists`, `search`, `activity`, `coverage`) for
  the view it was reached from.
- For the Graph: `tab=structure`, `around` or `order` (Structure opens when there is none or the
  tab is not one of these, so its address has no `tab`; an older `tab=timeline` opens Structure), and
  `focus=` with a record, `story:12` for instance (no `focus` means the whole project). Each tab
  keeps its own choices: `scope=`, `level=`, `labels=`, `colour=` and `links=1` for Structure;
  `reach=` and `wide=1` for Around; `mode=chains`, `phase=`, `chain=`, `budget=`, `blocked=1` and
  `pick=` for Order.
- For Lists: `view=work`, `requirements` (the acceptance criteria), `notes` or `everything`, with `q=` and `epic=`.
- For the Roadmap: `group=` and `zoom=`.
- For Search: `search=` and the other `search_...` choices, and `dups=1` for
  possible duplicates. For Activity: the `act_...` choices for its filters.

For example, `#/graph?labels=always&focus=story:12` (Structure, with every name shown and S-12 chosen) or `#/graph?tab=around&focus=note:7`.

What follows the `?` before the `#` is not a choice of yours. A provider that sends you back adds `?sso_error=` with a code (and `sso_provider=`), or `?sso=linked`, to the board's address. The board reads it once, says what it means (see [When a sign-in does not work](#when-a-sign-in-does-not-work)) and takes it out of the address.

## It is the same graph

A change on the board is the same operation an agent calls, with the same checks. If you drop a card
where it cannot go, or edit something an agent has changed since you read it, the board says what
the server says, in the same words, and keeps what you typed. How often the board asks for news is
`VRUM_BOARD_POLL_SECONDS` (5 by default).
