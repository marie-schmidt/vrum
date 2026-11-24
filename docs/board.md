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
  [Account page](#users-and-identities)). Every refusal says what is wrong, and a wrong password and
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

- **The sidebar** (left) holds the project switcher (with your role in the project; its last choice, *All projects…*, goes to the list of your projects), the four views,
  the epics of the project as a tree, and, at the foot, **Needs a person** (with the number of things waiting for a person, drawn only while there are any, see
  [Needs a person](#needs-a-person)), **Activity** (with the number of items someone holds right now), the **Settings and tools**
  menu and the **account menu**: a person icon and your name, which opens **Account** (your name, email and ways to sign in), **Appearance**
  (Light, Dark or System) and **Sign out**. The views carry no numbers: the page itself says how much it holds. The tree shows
  only on the pages about work (the four views, Search, Activity, Needs a person, the review page of a change, Coverage and the page of a record) and is out of the way on Account, Users, Definition of done,
  Plan templates, Tools and Status. Each epic opens to its stories and each story to its tasks (only what
  you open is drawn), the item you are on is marked and its parents are open, and choosing one opens its page.
  In the tree the arrow keys move, Right opens a branch, Left closes it and Enter opens the page.
  Choosing another project in the switcher loads that project's board afresh, so nothing of the
  first can appear in the second. **Sign out** ends the session on the server.
- **The top bar** holds the breadcrumbs, the **search box** (press Enter to search; the key of the quick switcher
  is beside it: *or ⌘K*, or *or Ctrl K*, which goes away while you type), and, only when there is something to say, a note on the state of the
  search index (items waiting to be indexed, an index not built yet, the embedding service not
  answering, or no search by meaning set up) and, when the server cannot be reached, a warning
  that says so on whatever page you are on. An
  index with nothing waiting, and a service that answers, say nothing here: Status has the whole account. The breadcrumbs start at the view and keep
  where you came from: on the page of a task opened
  from the Graph they read *Graph / E-3 / S-12 / T-40*, each of the three
  by its code alone (rest the pointer on one to see its title; on the page of an acceptance criterion or a
  note the last step is its short code, *AC-3* or *N-7*), and the
  Graph stays marked in the sidebar. The project is named by the switcher, so it is not in the path. On the page of a record there is also a **back arrow** at the
  left of the bar: it goes exactly one step back, to the page you were on just before, as you left
  it (another record, a Roadmap with its filter, a search, a settings page), the same as the
  browser's Back button. It still works after you reload the page. If the page was opened from an
  address, with nothing before it, the arrow goes to the view the record belongs to. The view's
  step in the path always goes to that view. On a window too
  narrow for the whole path, a record's page keeps the view, the record above it and the record.
- **There is no side panel and no drawer.** Every epic, story, task, acceptance criterion and note
  opens as a **page of its own** (see [Pages for every record](#pages-for-every-record)), and Search
  and Activity are pages too. A page keeps the view you came from, and the way back returns to that
  view as you left it, with its filter.
- Changing the Graph's tab, switching the kind of list, or changing the appearance never discards
  what you were typing.
- **A narrow window** (under 1100 pixels) hides the sidebar. The menu button in the top bar opens it
  over the page, and Escape, a click outside it or choosing a place closes it again. The account menu
  (and so the appearance choice) is in it, and the search box moves to a row of its own on a phone.

**Appearance.** Choose Light, Dark or System in the account menu at the foot of the sidebar (before you sign in, the same three choices are in the top bar). The choice is kept in this browser, never with any credential, and
signing out does not clear it. System follows the operating system's setting, also while the page is
open. Changing it reloads nothing, so a filter, a selection or an unsaved edit stays as it is. A
focus ring is drawn only when you use the keyboard (Tab or the arrow keys), in the text colour, and
never after a click.

**Status colours.** A status has one colour everywhere it is drawn (chips, the Board's columns, the status menus, the Roadmap's bars, the Graph's marks): New is grey, Ready is blue, In progress is violet, Blocked is red, Done is green and Dropped is grey, faded or dotted. Each also has its own sign, so colour is never the only clue. A late due day is red with an alert sign, the same on every page. Amber is for high priority and schedule conflicts.

**Calm by design.** Each fact has one home. A card, a row or a page shows what changes a decision now, and a default says nothing: a *Normal* priority, a story with no tasks, an empty list. An empty section is left out unless there is something to do in it (an empty **Details** field, *Add an acceptance criterion…*, *Add link…*). What a face leaves out is still one hover or one click away: a tooltip, the page of the record, its History, or the Agent view. Items are named like this everywhere: a letter code (*E-3*, *S-12*, *T-40*, *AC-3*, *N-7*) followed by the name, and sentences name an item by its name in quotes. A page has one title: the top bar names the page, so the page itself repeats nothing (its heading is still there for screen readers), and a page's header row holds only what can be done there. **A dialog or card that opens from a button opens empty and closed**: no list, menu or calendar is open, and a choice that means a decision (how two items are linked, what to create, a person's role, what an agent may do) starts on a quiet *Choose…* line instead of the first option; a dropdown opens when you click its field, and if you press the button without choosing, the dialog says in plain words what to choose and keeps what you typed.

## Board

One column for each status and one lane for each epic. A lane's head is the epic's title and code, how many of its tasks are done (with a bar) and **New story**; its status, dates and points are on the epic's own page. A card is a task or a story: its code and title, and then only the signs it has, small and quiet, because a person scanning a board needs what changes a decision and nothing that is the default:

- a **priority** arrow, only when the priority is high or urgent (a normal or low priority shows nothing; the page has it, and the arrow is a menu that changes it);
- its **size** (a letter, *XS* to *XL*; a story that has tasks shows what they add up to, *14 pts*); an open story or task with no size of its own shows a faint empty size sign, the same ones `no:size` finds, and a story that has tasks, finished work and an epic show none;
- **one progress sign**, *3/8*, that adds the acceptance criteria met to the checks of the [definition of done](#definition-of-done) ticked so far (a tooltip says which is which; it is green when all are done, and left out on a card that is done);
- the **due day**, red with an alert sign when the item is late (rest the pointer on it to see how many days), and a **schedule conflict** sign when it is planned to start before something it needs is due;
- a clock when the card **waits** for other work (that is not the **Blocked** status, which a person sets); rest the pointer on it, tab to it, or tap it to see what it waits for, each item with its code and title. The same one clock also says a card is **held** by a gate or by a plan that waits for approval, and its card names each hold (*Gate 5: Waits until …*, *Plan 3 waits for a person to approve the plan*); see [Gates and held work](#gates-and-held-work);
- a **handoff** sign when its item has a handoff note, and a small chip with the name of whoever **holds** it (the tooltip says until when). Rest the pointer on that name, or tab to it, to open the holder's card: it lists what they hold, and under each item, only when the holder said so, **where the work is** (the repository, branch, commit and pull request) and **which run it is** (see [Where the work is](#where-the-work-is)). A claim that said nothing has a card exactly as before, and the card itself never grows a line for it.

The card does not say "Done" or "Dropped": the column does. A story or task card ends with one quiet line, the story it is under (*S-5 Environments and release pipeline*); a story's card has its tasks-done bar instead. To change a status, **drag the card to another column** of its own epic; that is the only way to do it on the Board (a record's own page has a status menu too). Dropping a card into **Blocked** or **Dropped** asks for a reason first (see [Saying why](#saying-why-something-is-blocked-or-dropped)). *Ready* is
worked out from the links, so nothing can be dropped there.

The small **Key** button at the end of the row of controls opens a card (rest the pointer on it, tab to it, or tap it; Escape closes it) with the six statuses and what each means, and every sign a card can carry, drawn as the card draws it.

The **chevron** at the top right of a card folds it away to its code and its whole title (the
chips, what it is under and its notes go), and
shows it again on the next click. A folded card stays folded while you stay on the Board, whatever
the board redraws, and is open again after a reload. A card's code says what it is (E-, S- or T-); the small sign for its kind is drawn only when the Board shows stories and tasks together.

**Show** (above the columns) chooses what the cards are, as one choice out of three:

- **Tasks**, the default: only tasks, in their columns and under their epic's lane, each saying which
  story it is under. This is the day's work.
- **Stories**: only stories, each with a progress bar and "N of M tasks done" (a story with no tasks
  has neither). This is the planning view.
- **Both**: every story and task in the same columns.

The count in each column and the words of an empty lane follow the choice, and the help on
filtering explains it. A filter that names a kind
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

What you choose is the *focus* the tabs follow, and a trail with **Back** returns to what you looked at before. With nothing chosen each tab shows what makes sense for a whole project: Structure the map of everything, Around the epics as cards (how far along each is, what is under it and how many notes are linked) and what needs attention, and Order only a line asking you to choose an epic, story or task above (it plans one epic's work, so it never picks one for you). One click on a card looks around that epic.

- **Structure**: the whole project at once as a living map, in the way of Obsidian's graph view. The project is in the middle, each epic a hub, each story a smaller hub around it and each task a small dot around its story, joined only by their parent-child lines. The dots are flat discs in the colour of their status (the arc round an epic or story shows how much is done, a dashed red ring marks late work and a solid red ring blocked work), sized by how much hangs under them. The map is not drawn on a grid: the dots push each other apart and the lines pull them together, so nothing overlaps, tasks end up outermost, and epics settle at different distances from the project, some closer in and some pushed out by the room the others take. It settles within about three seconds of opening and then stops completely: nothing moves, and nothing runs, until you touch it. Grab any dot (an epic, a story or a task) and move it: it follows the pointer, the dots joined to it follow it and the others keep clear; let go and it eases back to where it was (nothing is saved, and a press that hardly moves is a click and opens the item). The wheel zooms toward the pointer, dragging the background moves the map and it carries on a little when you let go, and resting the pointer on a dot fades everything except that item, the item above it, the items under it and the ones it is linked to. With reduced motion switched on in the system, the map is drawn settled and does not move. **Forces** opens four sliders as in Obsidian (Centre, Repel, Link force, Link distance) with **Reset**; they last until the page is closed. **Tidy** glides the map back to the even, crossing-free layout it starts from. Task dots carry no text: names appear as you zoom in (**Labels**: Off, On zoom or Always), and a hover card gives the code, title, status and who holds it. **Show** limits it to epics, epics and stories, or everything; **Colour by** is status or epic. **Show links between items** draws the links that cross the hierarchy (needs first, duplicate, related, replaced, named in a text), and an item chosen in the box at the top always shows its own links, with the items it joins highlighted. **Scope** (the first control) chooses the whole project or one epic alone, with every name shown (that one is a fixed, tidy picture and does not move). A click on a hub or a dot, or Enter on it, opens that item's page; the arrow keys walk the tree; the map always has the whole width. The **Key** button at the end of these choices opens a card (rest the pointer on it, tab to it, or tap it; Escape closes it) that draws the map's own marks: an epic with its green arc for how much is done, a story, a task, the red dashed ring of late work and the solid red ring of blocked work, then each colour of status with how many dots of that colour there are, and the link kinds when links are shown or an item is chosen. Every other key in the Graph is a **Key** button too.
- **Around**: the focus in the middle with what is joined to it in labelled groups (part of, contains, needs first, blocks, acceptance criteria, **Notes**, similar, and the project-wide notes folded into one box). The **Notes** group lists the notes that apply, each saying its status (Unconfirmed, Confirmed, Replaced or Withdrawn); who decided a note, if someone did, is a detail on it. What is linked to the story or epic above the focus comes down to it too: those entries say "via E-1" (the item they are linked to). A task also shows the acceptance criteria of its story at one step, marked "via S-1", and a story its own; an epic's stories' and tasks' acceptance criteria appear at two steps. Replaced and withdrawn notes are struck through and unconfirmed ones are dashed. **Reach** shows one or two steps. The picture has the whole page, down to the bottom of the window: it is scaled to fit the tab without scrolling (a group shows its first few entries and **Show more** makes it grow where it is, with the whole picture zooming out or moving up to make room, never covering anything; **Show fewer** or Escape closes it again), and the **Fit**, **+** and **-** buttons beside the key zoom in to read closely. A click on a neighbour that is work re-centres on it, a click on an acceptance criterion or a note opens its page, and **Open page** on the item in the middle goes to its page. A button that says **Look around** wherever it appears has the same sign, five dots, and opens this tab for that item. The **Key** button beside **Reach** explains the lines.
- **Order**: every piece of work of one epic that is still to do, in phases. The epic is the focus's epic (with a story or task chosen, that item is highlighted in it); with nothing chosen (or an item that is not there) the page only says *Choose an epic, story or task above to see its order.* A project with no epics says there is nothing to order yet. Work is the epic's tasks, and its stories that have no tasks. **Phases** is the default: phase 1 is everything that can start now (work with no link is here), and each later phase is what becomes possible when the work it waits for, in the earlier phases, is done. A done or dropped prerequisite, or one in another epic, never delays anything, and a "needs first" link between two stories counts for the tasks under them. One phase is on the page at a time, with a stepper (**< Phase 2 of 5 >**, which the arrow keys also move) and the address keeps `phase=2`. A phase has its item count and its points, and its work is grouped by story (one story is one shared context), each item with its code and title (a link to its page), status, size, due date and, after phase 1, **Waits for** the items that hold it, as buttons that go to their phase and light them. Work that is done is in a folded **Done (n)** group above the phase; dropped work is not shown, only counted. One quiet line above them sums up what is left (*22 to do, 57 points, 4 not sized, 1 dropped not shown*). With sizes, **Fit a run of up to** 5, 8, 13 or 21 points (8 by default, `budget=` in the address) cuts a phase into numbered batches ("Batch 1: 7 points, 3 items") that are filled in story order and keep a story's items together where they fit; an item bigger than the budget is a batch of its own, marked **Bigger than a run**. Batches are only a way of looking: nothing is stored. Work with no size is listed apart under **Not sized yet** and is never guessed; with no sizes at all, a phase shows only its items. **Chains** (`mode=chains`) shows the "needs first" arrows from left to right, one chain at a time with its own stepper (**< Chain 2 of 4 >**, `chain=2`), then, as the last stop, **Loose items**: the work no link names. **Highlight what is blocked** lights up what is blocked and what waits for it; resting the pointer on an item in a chain, or tabbing to it, lights what it needs first and what waits for it, and a click on it opens its page. The arrow's colour tells how the work it needs stands (grey, green when that work is done, red when it is blocked). The **Key** button beside these choices names the phases, the sizes and their points (extra small 1, small 2, medium 3, large 5, extra large 8), the statuses and what overdue means, and, for chains, a dashed box (outside the chosen epic), the red of blocked work and the highlight of the chosen chain.
The tab, the focus and each tab's choices are in the address, so a view can be shared. Each tab is its icon and name; the page's one **Look at** box is the only place to choose a focus. There is no side panel on any tab: a click on an item opens its page, where the **Agent view** shows what an agent receives for it.

## Lists

The same records as plain lists you can filter. Choose *what*: **Work** (epics, stories and tasks as an outline you can open), **Acceptance criteria** (grouped by the story or task that holds them, those placed nowhere first, with the coverage report) or **Notes** (one table, see [Notes](#notes)); each button carries how many there are in the epic you chose. The filter box takes the [filter language](#the-filter-language) and has its own **Apply** and **Clear** buttons; an epic box narrows to one epic, and a checkbox shows the `[[references]]` written in texts. On a window wide enough (about 1200 pixels, or about 950 where there is no sidebar) the kind, the epic, the filter box and its two buttons are on one line, the filter box giving up some length to make room (never less than about 14 rem); on a narrower one the filter takes the line under them. The title of any row opens its page. A row of **Work** is an icon and a code (no word for the kind), the title, its size (a letter for a task, *23 pts* for a story with tasks or an epic) and its status, and under the title the same quiet signs a Board card has: the progress sign (*3/8*), the due day (red when late) and whoever holds it.

Lists start on what is current: **Notes** on the notes in force and **Acceptance criteria** on the open and met ones. Under the filters a quiet line says what that leaves out, with a button to show it (*2 replaced or withdrawn notes are not shown. Show them.*); it is not there when nothing is left out.

## Naming items

Epics, stories and tasks are numbered separately, within a project, so a code names exactly one item there. The board writes an item as a
letter and its number, always followed by its name: **E-3 Platform foundations**, **S-12 Rider accounts and sign-in**, **T-40 Sign-up form**. Acceptance criteria
and notes have a letter and a number of their own: **AC-3** (acceptance criterion 3) and **N-7** (note 7), also followed by the criterion's title or the note's
headline. A question an agent put to a person is **Q-12** (see [Needs a person](#needs-a-person)). Agents, the address and the text of a note name an item as the kind and number: `epic:3`, `story:12`, `task:40`, `rule:3`, `note:7`. What a person
never reads is a bare number in either shape (`#7`, `task 40`, `story 12`, `ask 12`): where a sentence needs an item it names it by its name in quotes
(*“Slot picker” is now blocked*), and when a refusal from the server names a reference, the board shows the item's name instead (and *Q-12* for a question). You can still type a code
(`S-12`, `AC-3`) to find something in the quick switcher or a picker.

## Pages for every record

Every epic, story, task, acceptance criterion and note has a page of its own
(`#/item?item=story:12`, `epic:3`, `task:40`, `rule:3`, `note:7`). It opens from a
card, a Graph row or dot, a roadmap bar, a search result, the activity feed, a `[[reference]]`, a
line of history or the quick switcher (see [Jump to](#jump-to)), and it says which view you came from (`from=` in the address).

**An epic, story or task** page has:

- At the top, its title and **one row of properties** with only what has a value: its status and priority (menus, drawn
  even when the priority is Normal), **size**, an epic's or a story's one progress, dates and who holds it (since when, and until when). Who
  created it, when it last changed and which version it is at are in History (a refused save speaks of
  versions), and what it waits for is in Links. **Size** is a menu on a story or task (Extra small 1 point, Small 2, Medium 3, Large 5, Extra
  large 8, and *No size*); a size not given yet shows as a dashed **Not set**, which is also the way to give
  it one. An epic has no size of its own, and neither does a story that has tasks (it adds up theirs), so
  those show **Points** instead: *6 of 17 points*, a small bar of the points that are done, and *4 not sized* when some
  of what is in it has no size yet (an epic or story with no sizes at all shows *14 of 35 tasks done* in the same place: one progress, never both). **Start** and **Target** are buttons: click one and
  choose a day in the calendar (see **Choosing a day** below); it is saved at once, and a late Target says *3 days late* in red. When you hold
  the claim there is **Release…** (it asks for a note for whoever takes the work next). When the holder said where the work is, one quiet **Where** entry follows
  **Held by** (see [Where the work is](#where-the-work-is)); work that nobody said anything about has no such entry. **Edit** opens
  the form for the title, details, status, priority, size and dates in place of the text (at the version
  you saw): **Save changes** keeps it and **Cancel** leaves it as it was. If an agent changes the
  item while you are editing, the page says so and what you typed stays.
- **Agent view** (a button in the header of every record's page, also for someone who can only look)
  opens a dialog, *What an agent receives for T-5*: exactly what an agent is handed for this item
  by `recall` (the notes, the project-wide notes, the earlier notes, the acceptance criteria and
  what is alike in meaning), or by `get_note` for a note. A piece of work that says where it is
  also shows the same **where** line as the page, under the call that reads the item; work that says
  nothing has no such line. A piece of work that has questions also lists them (**asks**), each with its
  question, its answer when it has one and the comment that came with it, in full. A story or task that was found while doing other work
  lists what it was **discovered from**, the same list an agent has in its briefing when it takes the item. **Close** or Escape leaves it; nothing is
  kept in the address.
- **Previous and next** buttons (also the `[` and `]` keys) with "2 of 12": the epics of the
  project, the stories of the epic, or the tasks of the story, in number order. The path in the top
  bar links up to the parents, and the table below leads down, so you can go from an epic to a
  story to a task, back, and on to the next epic without returning to a list.
- One page, one column: there is no list of sections to jump to; each section has its heading with how many it holds
  (acceptance criteria say how many are met, *1 of 3 met*), and a section with nothing in it is left out unless there is
  something to do in it.
- Its **Details** as text, with no heading of its own; when nothing is written they are an empty field (a dashed box that says
  **Empty**) with **Add details**, which opens the editor. Then, when an agent has put any, **Questions** (see
  [Needs a person](#needs-a-person)), and **Handoff notes**: every one the
  item ever got, newest first, each with who left it, when, how the work ended, where the work was
  when it was said (only when it was said) and the words in
  full, and **Handoff notes from what it needs first**: the last one of each thing it needs first,
  which is what an agent that takes the work is told. After them the place to move it and, when there is any, what
  mentions it.
- **A table of what is under it**: a story's tasks, or an epic's stories with their progress (a priority is drawn only when it is not Normal).
  Each column's heading sorts the table, the **Size** column included (a task's size is a menu in its
  row), and a line under the rows adds the sizes up (*23 points*, *4 not sized*). **New task** (or **New story**) adds one and opens it.
- **Links**, one section for every link of the item in both directions: a table grouped by what the link means (needs first,
  needed by, relates to, duplicates, replaces, replaced by, discovered from, led to), each row the other item's code, title, status, priority, who holds
  it and due day. **Discovered from** says the item was found while working on the other one, and **led to** is the same link read from the other end; it
  is a quiet note about where work came from, so it never makes anything wait and only a needs first link is drawn as an arrow on the Roadmap or the Graph. **Add link…** opens a small dialog (the kind of link, then the other item by its code or title) and **Remove**
  takes a link away. With no links a person who can write sees only the heading and **Add link…**; someone who can only look sees nothing. (Who added a link is in the History.)
- **Gates**, when the story or task has any, and for a person who can write on work that is not finished, a small **Add a gate…** button (see
  [Gates and held work](#gates-and-held-work)). A viewer sees the gates there are and no button; an item with no gate and no way to add one has no section.
- **Where could it go?**, a small button on a story or a task (not an epic) that asks which epic, or which story, it is nearest to by meaning (see
  [Where could it go?](#where-could-it-go)). Nothing is asked until you press it.
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
  says *4 of 6 ticked*. An agent ticks the same boxes, and has to
  say how it checked; an agent cannot finish a story or task as done while a box is unticked, but you can.
  A check whose line starts with **[person]** shows a **Person only** chip with a lock: only a person can
  judge it, so an agent can neither tick nor untick it (it finishes the item as blocked and says in its
  handoff note what to look at), and you tick it like any other. A check that starts with **[verifier]**
  shows a **Verifier** chip: you can tick it, and so can an agent whose key is marked a verifier, if it did
  not do the work on the item itself. One that starts with **[github]** shows a **GitHub** chip and is the
  same, and an agent's evidence for it starts with the address of a GitHub pull request, which the page
  shows as a link (it opens in a new tab). Under it a quiet line says *Reported by* the agent, *not
  confirmed by GitHub*: Vrum never asks GitHub, it records what the verifier says it saw, so the link is
  how anyone can look again. A tick by a person, and any other check, shows only its evidence as written.
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
  last (who created it and when is that last row). A change to the details or to a long title has **Show what changed**: the value before
  and after, whole and as it was written.
- **Similar**: what is alike in meaning but not linked (the same section on every kind of page), each with how alike; it is left out when there is nothing.

**An acceptance criterion or a note** page is cut down in the same way: its title (*AC-3 A rider can have only one open ride* or *N-7 …*), then **one row of properties**.
For an acceptance criterion that is its status (the story or task it is on is in the path at the top); for a note it is its status chip (the menu that confirms and un-confirms),
*Project-wide* when it is, and who decided it (*Decided by Lena Okafor on 2026-08-25*, a detail of the note, not another kind of note). Below that: its
**Details** (a criterion; an empty field with **Add details** when nothing is written, as on an item's page) or **What it says** and **Why** (a note), a table of
what it applies to (each row opens that item) with **Change what it applies to…**, what replaced it or what it replaced (only when
there is any), what mentions it (only when there is any), **Similar** and its history, with the buttons described below (**Edit**,
**Replace**, **Withdraw**, **Put on a story or task**). A **Confirmation** card appears only when there is something to say: the note is confirmed
(who, when, and in their words), or it ended after it was confirmed; an unconfirmed note's chip says it all. A note has one **status** (**Unconfirmed**,
**Confirmed**, **Replaced** or **Withdrawn**). A note has no
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
refused if it tries. A line that starts with **[verifier]** (`- [verifier] Reviewed by an agent that did not write it`)
is a check that a person, or an agent whose key is marked a verifier (see [Users and identities](#users-and-identities)) and that did not do
the work on the item itself, can tick; it shows a **Verifier** chip. A line that starts with **[github]**
(`- [github] The pull request is merged and its checks passed`) is the same, and the verifier's evidence has to
start with the address of a GitHub pull request; it shows a **GitHub** chip. A check carries one chip: a
**[person]** tag outranks the others, and **[github]** already means **[verifier]**. The tags can be written in
any order after the dash (and the box, if there is one). A tag is not part of the check's words, so adding or
removing one keeps every tick. The page's **Key** button shows the syntax, each with its chip, and each
check in the list shows its chip beside the number of stories and tasks it is ticked on. It is under
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
along it. Group by epic (the default: an epic's own row heads its group), status, priority or who holds it, and filter with the
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
  timeline: the colour, pattern and icon of each status, with what each means; the day cell, the weekend and the late
  tail; a pale dashed bar worked out from what is under it; the amber outline of a schedule
  conflict (also marked at the end of the item's row); the status icon that stands beside a bar
  too short for its words; a bar chosen to move with others and the ends you drag, and the keys
  above (only if you may change things); the line for today and the two kinds of arrow; and the
  rows of an epic, a story and a task.
- Every move is a change at the version the bar was drawn at, so one an agent changed since is
  refused and the bar goes back, and a start after the target is refused in the server's words.
- **It fills the window.** The timeline reaches the bottom of the window with its own scroll, and the
  faint grid (days, weekends, month lines and today's column) carries on below the last row, so a
  project with few items is not a small box on an empty page.
- **The hand means "drag".** The open-hand cursor shows only on a bar you may move (and on the empty
  grid, which you can drag to move along the timeline); labels, chips, the ruler and bars worked out
  from what is under them keep the arrow, and a person who may only look gets no hand on bars.
- **A status symbol says what it means.** Rest the pointer on the status icon in a bar, or on the
  one beside a bar too short for its words, and a small card names the status and what it means (the
  same words as on the Board); it does not open while a bar is being dragged.
- Choose a bar (or a row's label) to open its page. The roadmap has no sprints.

## Search and possible duplicates

The box in the top bar opens the **Search page** (`#/search`), over the `search` tool (press Enter; the page has its own box too). A search
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

**Possible duplicates** is the second tab of the Search page: pairs of stories (or epics, or tasks, or
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

Press Ctrl+K (⌘K on a Mac) on any view; the search box in the top bar says so beside it
(*or ⌘K*). Type a word of an item's
title, its code (`S-12`, `story 12`, `AC-3`, `N-7`) or the name of a view,
page or settings page, and Enter goes there.

## Activity

The **Activity** link in the sidebar opens the Activity page (`#/activity`). At the top is a toggle with three options, and the one you choose takes the
whole page: **Changes**, **Working** (who is working on what) and **Free to take**. The choice is in the address (`tab=changes`, `working` or `free`; no `tab` is
Changes), and the filters below stay as they are when you switch.

- **Changes** is what happened, newest first, in plain words ("Ada moved the criterion AC-7 from story S-3 “Rider accounts” to story S-5"), with a handoff note shown in
  full. When a claim, a renewal or a handoff said where the work is, the sentence says it, whole: "took task T-4 “Slot picker” until 6:57 PM, on branch feature/slots, at commit a1b2c3d, in pull request 12, in run run-2026-11-22-a (claude-code)", with the pull request a link only when it plainly is one. The actor of the sentence is still the key's name; a run label is only part of what was said.
  New changes appear within seconds with no reload. The **Filter** button opens the filters: by who, by kind of change (work,
  links, claims, handoffs, acceptance criteria, the definition of done, checks, notes, questions, gates, plans and templates, suggested moves), by item and by day (UTC); the button counts the ones that are set, and they open on their own when any is. A size change reads
  "sized “Slot picker” as Medium" or "took the size off “Slot picker”". A tick reads "ticked “Tests pass” on “Sign-up form”: 212 tests pass" (or "unticked"), with **Show what changed** when what was said of how it was
  checked is long; when someone ticks several checks of one item in a row, the feed folds them into one row, "ticked 7 checks on “Permits for 20 more stations”", with **Show the checks** to list each one with how it was checked, in full. The filter is in
  the address. A change to details or to a long title has **Show what changed**, with the value before and after in full; a claim that was released with a note reads "handed the work back".
  An event in the feed opens its item's page.
- **Working** is one list by person or agent: each has a state (working, lapsed, left a handoff note), the items they hold with a countdown (a claim that ran out says so in the same entry), and their last handoff. Under a claim's countdown, and under the last handoff, is what the holder said about where the work is and which run it is, when it said anything (see [Where the work is](#where-the-work-is)). You claim an
  item as yourself (by its name or code, or from the work that is free to take), and you can renew or
  release the claims you hold, with a note: the same operations an agent calls. A claim belongs to
  whoever took it, a person or an agent, and only that one can renew or release it, so the other
  claims have no buttons (the page of the item says who holds it). The countdowns tick only while this view is shown.
- **Free to take** is the work that is ready and nobody holds, ten at a time with **Show more**. A claim that ends moves to *lapsed* by itself.

## Needs a person

An agent that cannot go on without a person says so with a question (an *ask*) on a story or task: what it needs decided, optionally the answers on offer (two to eight), or a plain yes or no (an *approval*). It also says whether the work waits for the answer (it was marked blocked) or stays free to take while the question is open (the agent went on to something else). A question is numbered, **Q-12**, and it, and the answer to it, are recorded and never edited, so a later reader sees exactly what was asked and what was said. Only a person answers, once; an answer is final, and if something changes the agent asks again.

**The Needs a person page** (`#/needs`, from the sidebar, with the number of things waiting beside it) is a review. Everything only a person can settle is one *change*: a compact card in a group, with the verbs that decide it in one row at the foot of the card, and a page of its own (`#/review?change=…`) that shows the whole of it. The server works the list out, in which group each change belongs and what is asked of you; the page only draws it. The page explains nothing in sentences: a card is a short title, small signs that are data (who is waiting, for how long, what the decision does to the work) and a few lines of what the change is; what a button does is in its hover card, which opens on a pointer's rest and on the keyboard's focus, and a sentence you read is always somebody's own words (a question, a title, a reason) or the server's own refusal.

- **The top of the page** says how many you have viewed (*Reviewed 3 of 16*, with a small bar), has **Group by** (**Level**, **Topic** or **Decision**, see below) and the **Keys** button. Beside the changes on a wide window is a **rail**: every change, under the group it is in, each with its level, its code, a short title, a mark when it has something to weigh and a tick once viewed. Choosing one goes to its card and puts the focus on its header. Below 1100 pixels the rail is a drop-down, **Changes (16)**, and everything is one column at every width, so nothing is wider than the window. There is no summary sentence and no count of kinds: the groups carry their own counts, and they add up to the list.
- **Groups.** **Group by** is the server's own answer, row by row, and the page only puts the rows where it says: **Level** is how strategic the change is (*Plans*, *Epics*, *Stories*, *Tasks*, *Notes*), **Topic** is what it is about (the epic the work is under, or a set of changes whose work is alike in meaning, each group named by the server, and *Other* for what is about nothing the project can place) and **Decision** is what you are asked to do (*To approve*, *To answer*, *To look into*). The choice is in the address (`group=topic`; Level, the first, leaves it out) and never in the browser's storage. Two groups are the same in every way of grouping, and come after the others: **Routine moves** (see below) and **Waiting for their agent** (plans you asked changes for and the agent has not revised yet, one row each, closed). They are not counted in the sidebar's number or in *Reviewed 3 of 16*.
- **A card** is one change. Its header has a **kind chip** with the plain name of the kind and its code (*Plan 3*, *Suggested move* and *Proposal 7*, *Question* and *Q-12*, *Check* and the work's code, *Notes*, *Refused approval* and *Gate 5*, *Claim ran out*), a **level chip**, its **title** (a plan's own title; a move's is *Move S-58 and S-59 under a new epic, Visitors and tourists*, with the codes as links to those items; a question's is the work it is about), a **Viewed** box and **Open**, which goes to the change's review page. Under the header is a row of small **facts**: who is waiting, how long (by the server's clock), *Revision 2* from the second revision, what an answer does to the work (*Blocks work*, *Work stays free*, *Gates work*), what the decision moves (*Frees 7*, *Moves 3*, *2 open*, *2 notes*, *Ran out ×2*), how many warnings there are to weigh (*2 warnings*, *not checked*) and the work's size (*Size S*, with what that is worth in its hover card). Then a **preview**: never more than six lines, and then one fold that says how much is behind it (*54 more items*), and then the bar.
- **The bar** is one row at the foot of every card and at the foot of the review page, the same for every kind: its verbs, then **Comment**. A verb says what it does in a word; its hover card says what it does to the work and whether it can be undone, and a decision is always final, recorded under your name. There is no **Accept**, **Yes** or **Confirm** to learn: approval is **Approve**, refusal is **Reject…** and a reason is asked for in a small box.

  | Change | Verbs |
  |---|---|
  | A plan | **Approve**, **Request changes…**, **Reject…** |
  | A suggested move | **Approve**, **Reject…** |
  | A question with answers on offer | each answer is a button; **Comment**, and **Keep as a decision** under it |
  | A question that is a yes or no | **Approve**, **Reject…** (the reason is the comment) |
  | A question with no answers on offer | **Answer…** |
  | A check only a person can tick | **Approve** (ticks it as you; **Comment** says how you checked) |
  | Notes waiting to be confirmed | **Approve** (confirms: the box names you), **Reject…** (withdraws it with a reason) |
  | A refused approval | **Lift the gate…**, **Drop the work…** |
  | A claim that ran out | **Block it…**, **Drop it…** |

  A row of several notes or several checks has a bar under each, because one bar would not say which. A viewer sees the changes in full and no bar.
- **Keep as a decision.** Under the comment of a question is a box, **Keep as a decision**, unticked. Ticked, the answer is sent first and then recorded as a note of its own, decided by you, linked to the work, with your comment as its reason (*Answered Q-12.* when you wrote none). If the note is refused the answer stands, and the page says so in the server's words under **The answer stood, but the decision was not recorded**. Unticked, no note is made.
- **A plan that waits for approval** is in the group *Plans*. Its card previews the first six items it would make as added lines (`+`), each a link with its kind, code and title, and says how many more there are. Its review page shows the whole of it (see **The review page** below): the existing epic or story it hangs under, and the existing items it points at, as quiet lines that stay (`.`), and then **every item it made** as an added line, in the order the server gives (parents first) and nested: its code and title as a link, its kind, its size (or a faint sign when it has none), a priority only when it is not Normal, its start and target day, *needs T-185* for each item it needs first and its other links in words, and *criteria 2*. The item's details are added lines under it, and each acceptance criterion is a line with its code (*AC-3*) and title. A plan of more than 40 items folds each story's tasks behind a line that says how many (*12 more tasks*); a press opens them. **Approve** frees all of its work to be taken, as one decision. **Request changes…** asks the agent to revise the plan and frees nothing (see **Comments, requesting changes and revisions** below). **Reject…** opens a small box that needs a reason (the server says so in its own words if you leave it empty): rejecting drops all of the work and leaves your reason on each item. Approving and rejecting are final and recorded under your name; nothing in the plan can be taken until you decide.
- **A suggested move.** An agent, such as a steward that sorts loose work, may propose moving stories or tasks under another epic or story, or grouping stories under a new epic. Each proposal (**Proposal 7**) previews the work as added lines under its new place (*from E-9 Misc*). Its review page shows the proposer's reason in full as its message, then the new parent with its first two children for context and the moved work as added lines, a new epic as an added line with its details, and then the old parent or parents with the same work as removed lines. **Approve** makes the moves as you (each is an ordinary move in the history, which can be undone by moving the item back) and makes the new epic only then. An item that has moved or finished since the proposal is left where it is, and the page says so once, in the server's words (*S-9 left where it is: it was moved to E-5 since*) until you dismiss it. **Reject…** needs a reason, which goes back to whoever proposed it, and moves nothing. Each moved item also shows how alike it is to the parent it would go under (see **Scores and routine moves** below).
- **A question or approval** previews the whole question and the answers on offer. Its review page adds who asked, what the answer does to the work, and the work it is about. Pressing an answer is the answer: nothing is picked for you. **Comment** opens a box under the bar, and the comment goes with the answer you press. A question with no answers on offer has **Answer…**, which opens a small box with a field for your answer and one for a comment. When the server refuses an answer (a secret in it, an answer already given by someone else) it says so in its own words, under the bar, and nothing is stored. When it is answered, the card goes, and work that was blocked for it is ready again.
- **Checks only a person can tick.** Work an agent handed over as blocked, with everything done except checks of the definition of done tagged for a person. The card previews the checks waiting for you, each with its own bar; the review page lists all the checks of the item, ticked or not, with who reported each and how it was checked. **Approve** on a check ticks it as you, saved at once, as it is on the item's page; **Comment** is where you say how you checked, and the words are kept whole and shown under the check. A refusal (a secret in the words, say) is shown under the check in the server's own words, and nothing is ticked. The last check you tick takes the card away.
- **Notes waiting to be confirmed** on work that is in progress. The card previews the first line of each note, each with its own bar. The review page shows each note whole: its text, why it is so, who decided it (or that nobody did), who recorded it and when, what it concerns as links and whether it applies throughout the project. **Approve** opens the same box as on the note's page, empty except for your name: confirming records that a named person reviewed the note and what they said. It does not check that it is true, and changing a confirmed note later needs that person's word. **Reject…** withdraws the note with a reason. A refusal (the note was withdrawn meanwhile) is shown in the box, in full.
- **A claim that ran out twice** with no handoff note, which means something may be wrong with it. The review page lists the claims that ran out and the work's last changes. **Block it…** marks the work blocked and **Drop it…** marks it dropped, each with your reason as the work's note.
- **An approval that was refused.** Work held by a gate that waits for an approval stays held when a person answers no. The card shows the gate in the server's words as the line that lifting it would take away. **Lift the gate…** asks for an optional reason (see [Gates and held work](#gates-and-held-work)) and frees the work again, unless something else still holds it; **Drop the work…** marks it dropped with your reason.

**Checks on a plan.** A plan that waits for approval shows three checks the server ran, on its review page above the diff, so you do not have to find these things by reading sixty lines. A check that passed is not a line of its own: one quiet line says them all (*No circles*, *Every task sized*, *No repeated work*). One that did not pass gets a line with its state (**Warning**, **Failed**, or **Not checked**), the server's sentence, and each finding with the items it names as links.

- **No circles** finds items that need each other round and round. A circle is the one finding that is **Failed**. It never stops you deciding: the page tells, the person decides.
- **Every task sized** names tasks that have no size, as *T-11 has no size.* Nothing is sized for you.
- **No repeated work** names a story that looks like work that already exists, with that work as a link, its status and how alike it is (*S-14 looks 83% like S-1 Title, which already exists (ready).*). It needs search by meaning. When that cannot be used it says **Not checked** and why, never a quiet sentence that nothing repeats: not having looked is not a pass.
- **Flags** sit on the line of each item a finding names (*no size*, *may repeat S-1*, *in a circle*), and a **chip in the header** counts them (*2 warnings*). A change with something to weigh is marked in the rail. The checks are part of what a viewer reads.

**Scores and routine moves.** A suggested move's review page shows, beside each moved item, how alike it is to the parent it would go under, as git shows how much a renamed file still matches (*71%*). The number is the same one **Where could it go?** gives that item for that parent, as a whole percent, rounded down (a similarity below zero reads as 0%). A score is evidence, not approval: nothing moves until you press **Approve**.

- When the parent is not the best the server found for an item, the line says so (*ranks 2nd, best is E-1 Billing*). The lowest score of a move is marked, when the scores differ.
- A moved item that is held, in progress or already moved has a **Warning** on its line in the server's words (*S-13 is held by tests: moving it changes where work under way is.*). A card with a warning has *1 warning* in its facts and a mark in the rail. A move to a new epic has no parent to score against and says so.
- **Routine moves** are the ones the server calls routine: the best parent is clear for every item, nothing is held or moved, and nothing in it needs weighing. They are one group at the end of the page, **Routine moves** with their count, closed. Opening it shows every one of them as a whole card, the same as any other; the server's reason it is routine is on the review page. A move with anything to weigh is never in the group, and the group never hides a warning. Opening the group, or choosing one of its moves in the rail, is all the page does: each move is still decided with its own button. The group counts in *Reviewed 3 of 16*.

**Viewed** is your own mark: ticking it folds the card's preview (the bar stays) and counts the change in *Reviewed 3 of 16*, and unticking opens it again. It is kept in the page's memory only, never in the browser's storage, so it lasts until the board is reloaded, and it survives going to the review page and coming back and the page drawing again when news comes. When something about the change changes (an agent renames an item of the plan, or sends a new revision of it) the mark comes off by itself and the card says *Changed since viewed*.

**Comments, requesting changes and revisions.** Reading a plan is a conversation with the agent that drafted it. You can ask for changes without rejecting the plan, and the agent answers with a revision that you read again before anything is freed.

- **Comments on lines.** If you can write, every item line of a plan that waits for you, on its review page, has a small speech bubble at its end (*Comment on T-185*; faint until you point at the line or tab to it, and always there on a touch screen). It opens a box right under the line, empty: **About** (the whole item, or its title, details, size, priority, start day, due day, where it hangs, links or acceptance criteria) and **Your comment**, with **Add comment** and **Cancel** (Escape cancels, Ctrl or ⌘ with Enter adds). The comment appears under the line marked **Not sent yet**, with the words it is about as they are now (the item's title, or the text of that part) and what you wrote, and it has **Edit** and **Remove**. Nothing is sent to anyone by writing a comment: your comments live in the page, like Viewed, and the bar counts them (*3 unsent*), with a closed list under **Conversation** that takes you to each one. Details and acceptance criteria lines have no bubble: a comment is about an item, and *About* says which part.
- **Request changes…** opens a box that is empty and asks **What should change** (required: the server says so in its own words if it is empty), and lists the comments it will send, read only, in order. **Ask for changes** sends the summary and exactly those comments, with the revision you read, under your name. **Cancel** changes nothing and keeps your comments. The plan stays held, with the same number: nothing is dropped, nothing is freed, and the agent learns of it from the same record you read here and sends a revision. The plan goes from the list to the group **Waiting for their agent**, because it is the agent's turn, with one row for each such plan (who asked and how long ago, and its title as a link to its review page). A plan that waits for its agent is not counted in the sidebar's number or in *Reviewed 3 of 16*, and it comes back to its group when the agent has revised it.
- **A revision** comes back as the same plan with a chip, **Revision 2** (the first revision says nothing), and, under **Conversation** on its review page, the list of revisions with who made each and the agent's note on what it changed, in full. The way to read it is **Changes**: a row such as *Revision 2: 1 added, 2 changed, 1 removed*, then a `+` line for each item the revision added, a `-` line for each it took out (as it was) and a `~` line for each item that differs, with, under it, each part that changed as a `-` line with what it was and a `+` line with what it is (the title, the details, the size, the priority, the start and due days, where it hangs, the links, the acceptance criteria). The items that did not change are not drawn: a row such as *14 more items, unchanged* counts them, and one press opens the whole plan. **Whole plan**, beside it, is every item of the revision as an added line, as when you first read the plan, and the choice is yours for that plan until the next revision arrives. An item you commented on that did not change is drawn too, as a line that stays, so you can see that it was left as it was.
- **What you asked for before** is under **Conversation** and under the lines it was about: one section for each time you asked (*Your feedback on revision 1*, with how many comments, and the summary in full; the last is open when the plan was revised), and each comment is under the line of the item it was about, in both ways of reading, with the words it was about as they were then, what you said, who said it, on which revision and how long ago. A comment about an item that is gone is under its `-` line; one that has no line in the way you are reading is listed under its feedback, so nothing you said is out of sight.
- **Approving is one decision about the revision you read.** Every decision names the revision on the page. If the agent revised the plan while you were reading it, the server refuses the decision in its own words (*Plan 3 is at revision 2 now, and you reviewed revision 1: it changed while you read it.*), the words stay beside the buttons, nothing is changed, and the page reads the plan again at once so that you see the new revision. An item you asked to remove is dropped by the revision and is never freed by approving what remains. A plan whose items were changed outside a revision (somebody edited one) shows a red row above the diff (*Changed since revision 2, outside a revision: T-12 (title)*), and the server refuses to approve it until the agent sends a revision (asking for changes and rejecting still work).
- **If a revision arrives while you have comments you have not sent,** they are not kept, because a comment names the revision it was written on. The page says so once (**Revision 2 arrived**, *Not kept: 3 unsent comments*), with a closed list of what they said so you can write them again, and the mark of Viewed is taken off, as for any change. Dismiss it, or leave the page, and it is gone.
- **A viewer** reads all of it, the revisions, the threads, the feedback and the group of plans that wait for their agent, and has no bubble, no box, no list of unsent comments and no bar.

**The review page** (`#/review?change=plan:3`, from **Open** on a card, from a card's title and from a code of a plan anywhere on the board, such as the history, Activity and the clock on a held item) shows the whole of one change and is where it is read before it is decided. The address names the change (`plan:3`, `placement:7`, `ask:12`, `check:task:40`, `note:task:40`, `lapse:task:40` or `gate:5`) and may add `revision=2`, `mode=whole`, `view=split` and the way of grouping you came from (`group=topic`). It has:

- a **header**: the arrow back to the list as you left it, the kind chip, the level chip, the title and the same facts as the card, **Viewed**, a link to the work's own page, and, when you came from the list, **previous** and **next** with where you are in it (*3 of 16*), in the order of the list in the way of grouping you were shown;
- **Checks**, for a plan that waits (see **Checks on a plan**);
- **Changes**, the change itself. A plan or a suggested move is unified lines with a sign in the gutter: `+` is added, `-` is removed, `~` is changed and `.` is something that is there already, for context; the sign carries the meaning and the colour only helps. A row in the same colours says what a part is (*1 epic, 2 stories, 4 tasks added*, a move's new parent, *Revision 2: 1 added, 2 changed*); the row of a fold says what is behind it (*6 more items, unchanged*, *4 more tasks*, *Show 12 more lines*). A body is shown as it was written, line by line, never as Markdown: the first six lines, then a fold. Two choices change the lines: **Unified / Split** (for a plan and a suggested move only, since they are the changes that have a before and an after) puts **Before** and **After** side by side as two columns of the same signed lines, an added line with an empty before and a removed line with an empty after, and under 900 pixels the two columns stack; **Changes / Whole plan** (for a revised plan) reads what changed since the revision before, or all of it. A plan with revisions has a **Revision** select that reads every revision as it was submitted; an earlier revision decides and writes nothing. The choices are in the address, so a page can be shared and reopened. Any other change shows its parts as lists: a question in full with the answers on offer, the checks of the definition of done, each note whole, what a refused approval waits for and who refused it, the claims that ran out;
- **Context**, read only: where the work sits (the epic and story above it), its status, priority and size, who holds it and where the work is, its details, its acceptance criteria and its story's, the checks, the handoff note it was left with and its last few changes;
- **Conversation**, for a plan: its revisions, the feedback and the unsent comments (see above), and for a question who asked it;
- the **bar**, kept in view at the bottom of the page, for a change that you can decide.

A change that is not in the list any more, because it was decided or never existed, says *That change is not here*; a plan that was decided, or waits for its agent, is read there read only, with its state (*Approved*, *Rejected*, *Waiting for the agent*), who decided it and the reason in full. After you decide a change the page goes on to the next one in the order of the list, or back to the list from the last.

**Keys** (only on the list and the review page, never while you are typing in a field, in a drop-down, in a dialog or in the quick switcher, and never with Ctrl, Alt or ⌘ held). On the list: `j` and `k` go to the next and the previous change (its header takes the focus), `o` opens its review page (on the group of routine moves, opens or closes the group), `v` marks it viewed (or takes the mark off), `a` and `r` put the focus on its first and its Reject button without pressing it, and `?` shows the list in the **Keys** button. On the review page: `[` and `]` (also `j` and `k`) go to the previous and the next change, `u` or Escape (when nothing else is open) goes back to the list, `s` switches between unified and split, `m` between changes and the whole plan, `v` marks it viewed, `a`, `r` and `c` put the focus on Approve, on Reject and on the comment, and `?` shows the list. **No key decides anything**: a decision is final and only a press of its button sends it. After a decision the focus moves to the next change.

Nothing here is ticked, confirmed or answered for you. How long something has waited is counted by the server's clock. With nothing to review the page says *Nothing to review* and the sidebar draws no number. A viewer sees every change in full and no bar; an editor and the owner act, and what they do is recorded under their name. Anything that is only information stays in Activity.

**On the page of an item,** a story or task that has questions shows them under **Questions**: each with its number, the whole question, who asked and when, and a chip for what it did to the work (**Blocks work** or **Work stays free**, with what that means in its hover card), then either the answer (who gave it, when, and the words and comment in full) or, while it is open and you can write, the same answers as buttons. An item with none has no such section. History says it in plain words (*asked Q-12 about task T-4 “Slot picker”: “Which provider?” (choices: Stripe, Adyen)* and *answered Q-12 about task T-4 “Slot picker” with Adyen, saying: “Cheaper”*), and the Activity filter by kind of change has **Questions**.

## Gates and held work

A **gate** holds a story or task until something outside the graph happens. A **timer** gate waits until a day (and a time, in UTC); it opens by itself. An **approval** gate waits for a person to say yes: it is a question (*Q-12*) on the Needs a person page, and it opens only when a person answers yes. While an item is held it is not ready, nothing hands it out and nothing can claim it. A plan that an agent drafted holds all of its work the same way until a person approves the plan (see [Needs a person](#needs-a-person)). Gates and plans are named in words, as the server names them: **Gate 5**, **Plan 3**, **Proposal 7**.

- **On the page of a story or task**, the **Gates** section lists each gate with a count, the ones still in the way first: its number, what it waits for and where it stands (the server's own words, such as *Waits until 2026-12-03 00:00:00 UTC.* or *Waiting for a person to approve: May we touch billing?*), who added it and when, and, for an approval, the question's number. A gate that is still in the way has **Lift…** for someone who can write; one that is open or lifted has no button. **Add a gate…** is there for someone who can write, on a story or task that is not done or dropped (an epic cannot be held).
- **Add a gate** opens a box with nothing chosen: **Wait until a time** (a day with the calendar, and a time of day such as 09:30 if you want one) or **Wait for a person to approve** (the question the person is asked). The server checks the day (it must be still to come and within three years) and the question, and says what is wrong in the box in its own words.
- **Lift…** asks for an optional reason (its hover card says that lifting frees the work and cannot be undone); lifting an approval nobody has answered closes its question too. Only a person lifts a gate, and an agent's attempt is refused. It stays in the list afterwards as *Lifted by Priya: The date moved*, with no button.
- **Held work shows one small clock.** On the item's page it is in the row of properties, on a card it is the same clock that shows waiting for other work, and resting the pointer on it, tabbing to it or tapping it lists each hold with its words (a plan's code in that list is a link to the review page of the plan). It is the only sign: the status does not change. The filter `is:held` finds held work (see [The filter language](#the-filter-language)).
- **Nothing opens a gate for you.** Answering yes on the Needs a person page opens an approval gate; an answer of no leaves it refused, which is a change of its own there until a person lifts it or drops the work.
- **History and Activity** say it in plain words: *added Gate 5 to task T-3 “Slot picker”: waits until 2026-12-03 00:00:00 UTC*, *lifted Gate 5 on task T-3 “Slot picker”: The date moved*, *proposed Plan 3, 1 epic, 2 stories and 4 tasks: Winter fleet expansion*, *asked for changes to revision 1 of Plan 3: Name the stations (2 comments)*, *sent revision 2 of Plan 3 (removed 1): Named the stations and dropped the covers order*, *approved revision 2 of Plan 3*, *rejected Plan 3: Too big*, *saved the plan template station-launch*, *proposed Proposal 7: move …* and *accepted Proposal 7: moved …, left … where it is (…)*. Gates are in the history of the item they hold; plans, templates and proposals belong to no item, so they are in the Changes feed only. The **Filter** by kind of change has **Gates, plans and templates** and **Suggested moves**.

## Where could it go?

On the page of a story or task, **Where could it go?** asks the server which epic (for a story) or which story (for a task) the work is nearest to by meaning, and shows the answer in place, under the links. Nothing is asked until you press it, and pressing it again closes it; it forgets when you leave the page. It needs search by meaning (see [Search and possible duplicates](#search-and-possible-duplicates)); when the server cannot suggest, the box says why in the server's words.

- Each suggestion, best first, is the epic or story as a link with its code and title, its **score** (0 to 1, with a small meter: compare the scores of one answer with each other, not with another answer), how many items it already holds, and **the reason**: the items under it that the work most resembles, each a link with how alike it is. The first carries a **Sure** chip only when it is clearly ahead of the second; its card says it is the best of the model's guesses and still a guess.
- **Where it is now** says the present parent and, when the server could rank it, where it comes among the choices (*It ranks 3rd of the choices.*). An epic called Inbox, where unsorted work waits by convention, and the stories in it are never suggested.
- **Move here** moves the item under that suggestion, as you and at the version the page shows (a change made meanwhile is refused in the server's words, under the suggestion), and then the page follows. A viewer sees the suggestions and no Move button.

## Where work was found

A **discovered from** link says a piece of work was found while working on another: the new item is *discovered from* the old one, and the old one *led to* the new one. It is a note about where work came from. It never makes anything wait, so an item with only such a link is still ready, and an agent that takes the new item is told where it was found. The board shows it in the Add link dialog (**discovered from** and **led to**) and in the Links section of both items (with a small mark beside the words), as a thin dotted line with no arrow in the Graph (the Key explains it, and Around lists the other item among what is related), in the Agent view, and nowhere on the Roadmap, where only a needs first link is an arrow.

## Where the work is

An agent that takes work may say, in optional words, where it is doing it: the **repository**'s address, the **branch**, the **commit** and the **pull request**'s address. It may also say which run it is: a **run label** (such as `run-2026-11-22-a`) and a **client name** (such as `claude-code`). The words are kept with the claim and copied into its handoff note and its history, so a person, and an agent that takes the work over, can see where it stands. Vrum never reads a checkout and never asks GitHub: the board shows what the holder said, which is not something checked.

The board shows the words one way everywhere and shows nothing at all when nothing was said (work that was never given any looks exactly as it always did):

- **A card's holder.** The chip stays the holder's name. Resting the pointer on the name, or tabbing to it, opens the holder's card, and under each item it holds the card lists the run (*run-2026-11-22-a (claude-code)*) and then the place (repository, branch, commit, pull request).
- **The page of the item.** One quiet **Where** entry in the strip of properties, with the claim's words while someone holds it and the last handoff's words after. Each handoff note under **Handoff notes** shows the words that came with it. The history says it in the sentence of the claim, the renewal and the handoff.
- **Activity.** In Working, each claim, and each one that ran out, shows its words next to the countdown, and the agent's last handoff shows its own. The Changes feed says it in the sentence.
- **The Agent view** shows the same line for an item that has it.

A commit is shown as its first seven characters, with the whole hash in its tooltip. A pull request or repository address is a link, opening in a new tab, only when it plainly is one: an `https` address with no password, query or fragment, and for a pull request exactly the repository's owner and name followed by `/pull/` and the number. Anything else is shown as the text it is. A branch and a run label are shown as plain words, never as code. The run label and the client name are only a label, to tell several runs of one agent apart: who did something is always the name of the key, and a run label is never drawn as a person or an agent.

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

**The card of a person or an agent.** Wherever a name has the person or robot sign (the feed, the Working view, a Board card, a table, Members and Agents),
resting the pointer on it, tabbing to it or clicking it opens a small card: who it is (*a person in this project* with their role, or *an agent*), when a person
joined, their email if you are the owner, for an agent the owner sees when its key was last used, what it may do and how it calls Vrum (and a **Verifier** chip when its key is marked one), then **Holding now** (each item with how long
is left; **Ran out on them** when a claim lapsed) and **Last handoff** with the item and the first words of the note. A click on a name on a Board card opens only
the card, not the item. Escape or moving away closes it.

The **Account** page (`#/account`, from the account menu at the foot of the sidebar) is yours alone: your name (change it and
choose Save: it is how the history names you from then on, and what was recorded earlier keeps the name it was recorded under), your
email (it cannot be changed yet; when the server can send email it also says **Verified** or **Not verified yet**, with a **Send
verification email** button until it is verified, see [Email](#email)) and **Change password** (the current one, the new one and the new
one again: your other sessions end). When the server offers another way to sign in, or your account has no password, Password becomes
**Sign-in methods**: see [Connecting and disconnecting](#connecting-and-disconnecting). Below them are your **Email nudges** (see [Email nudges](#email-nudges)). **Sign out** is in the account menu.

The **Users** page (`#/users`, in the Settings and tools menu) is about the project and has two sections, and a third that only the owner has (see [Webhooks](#webhooks)).

- **Members.** The people of the project, each with their role. Everyone in the project sees the
  list, and the owner also sees their email addresses. The owner can **Invite someone**, change a
  member's role and remove a member; anyone who is not the owner can **Leave this project**. See
  [Members, roles and invitations](#members-roles-and-invitations).
- **Agents.** The project's agent identities, one for each key: its name with an agent sign, the start of the key,
  when it was last used, **what it may do** in words when that is more or less than the standard tools, the day it
  **expires** when it has one, **Change…** and **Revoke**; revoked keys are folded away under *Revoked keys*. **New agent key** opens a dialog that asks for the agent's **name**, which is what
  the history shows for everything that uses the key, so make one key for each agent; **Create
  key** makes it. A name is 1
  to 100 characters and unique among the project's active keys. The key itself is shown once, with
  a **Copy** button, and with what it may do: only a hash is kept, so a lost key is revoked and
  replaced. **Connect an agent**, once, at the foot of the section, gives the endpoint (with Copy), the header to send a key in
  and an example client configuration (see [connecting an agent](agents.md)). Only the owner creates, changes and
  revokes keys: an editor or a viewer is told so and sees only how an agent connects.

  **How it calls Vrum** is the first choice in the dialog and starts empty: *By running code* or
  *One tool at a time*, each with one quiet line about what it gets. Sending without choosing is
  refused in the dialog (*Choose how it calls Vrum.*) with the name kept. A key that runs code is
  given `explore` and `run` and nothing else: the agent writes a short script, and every tool the key may use
  is a function inside it (see [calling by running code](agents.md#calling-by-running-code)). A key that
  works one tool at a time is offered its tools, as before. Each key's row says which it is, in a
  quiet line (*Runs code* or *One tool at a time*) with when it was last used, and so does the card
  of an agent that the owner opens from a name. **Change…** also changes how the key
  calls Vrum, starting from its current way, and applies from the agent's next request. What a
  key may do (below) is the same in both ways: a key that may only read can only read inside a script.

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
  and work, 18 tools*, *Everything, 47 tools* or *Custom, 12 tools*. The owner can press **Change…**
  on an active key (its dialog has what the key may do, how it calls Vrum, whether it verifies and when it expires), which opens the same list in a dialog (the section of tools not
  offered is open when the key already has one of them); what is saved applies from the agent's
  very next request, even on a connection that is open now. An agent sees only the tools it may
  use, and a call to any other is refused in plain words with nothing changed. A key with no list
  of its own is told that the tool is not one of the tools Vrum offers agents, so the key (named)
  cannot use it: it is done on the board, by a person, and the project's owner can allow it for
  this key on the Users page. A key with a list is told that it is not allowed to use the tool
  and to ask the owner to change what the key may do. A revoked key keeps showing what it could
  do and cannot be changed.

  **Verifies other agents' work** is the box after the tools, unchecked when it opens, with one quiet
  line under it (*May tick the checks marked [verifier] or [github] on work it did not do itself.*). A key
  with it checked belongs to a *verifier*: its row shows a small **Verifier** chip (a hover card says what it
  means) and so does its name card; a key without it shows nothing about it. **Change…** has
  the same box, and a change applies from the agent's very next request. What it lets a verifier do is
  written in [Definition of done](#definition-of-done): tick a check tagged [verifier] or [github] on
  work it never claimed or handed off. The agent that did the work finishes the item as blocked, saying it
  is ready for a verifier; the verifier finds it with the filter `is:unverified` (see
  [The filter language](#the-filter-language)), ticks the checks and sets the item to done.

  **Expires** is the last field of the dialog and is empty when it opens: a key with no day never expires,
  and says nothing about it. Type the last day the key works (`YYYY-MM-DD`, in UTC) or choose it from the
  calendar. A key with a day shows one quiet chip, *Expires 31 Dec 2026*, in its row and on its name card;
  once that day has passed the server says so and the chip is the one red chip *Expired 30 Nov 2026*
  instead, never both. An expired key is refused in plain words on its next request, even on a connection
  that is open now, and the words say that the project's owner can extend it or make a new key. The owner
  changes or clears the day from **Change…**: the **Expires** field there starts with the day the key has
  (**Clear** empties it, and saving an empty field makes it a key that never expires), a day that has passed is
  refused in the dialog in the server's words, and while the key is expired the dialog says in one quiet
  sentence to choose a later day to extend it, or to make a new key. A change applies from the agent's
  very next request, so extending an expired key makes it work again at once. Only the owner sees the key
  list, so an editor or a viewer sees neither the chip nor a control for it.

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
Account page shows **Verified** or **Not verified yet**, with **Send verification email** while it is
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

## Webhooks

A **webhook** is a signed message that Vrum sends to an address when something happens that needs someone: a question or an approval waiting for a person, a drafted plan or a suggested move waiting to be decided, work blocked or finished, a claim that ran out, or work that may be ready to take. It is only a nudge. The message names the kind of news, the project's number, the event's number and the story or task's reference, and never what anything is called or what anyone wrote; the change feed stays the source of truth, so a receiver that missed one loses nothing by reading the feed. Webhooks are for waking a person or starting a dispatcher; an agent that is already running is better served by reading the feed. How a receiver checks a message is in [connecting an agent](agents.md), and the settings that allow an address on your own network are in [configuration](configuration.md#webhooks-and-email-nudges).

**Webhooks** is the last section of the Users page, and only the project's owner has it: an editor or a viewer sees no such section, nothing is asked for them, and the server refuses their calls anyway.

- **An empty section** says in one sentence what a webhook is and has **Add a webhook**. At ten webhooks, the most a project may have, the button is gone and a line says why.
- **Each webhook** is a row: its address as text (you wrote it and need to recognise it, so it is shown whole and wraps, and is never a link), then what it is sent for as small chips, one for each kind of news, with the server's sentence in the chip's tooltip, or one chip, **Every kind of news**, when it is sent for all of them. A webhook that is fine says nothing more. One that is not carries one chip: **Turned off**, **Failing** (the last message did not get through and none has since; rest the pointer on it or tab to it for what went wrong, in words, and when) or **Waiting to send 2** (messages that have not gone yet). While a replaced secret still signs too, a quiet line says *The old secret still works until 2026-11-24.*
- **Add a webhook** opens a box with nothing filled in: the address that receives the messages, and the kinds of news as boxes, each with the server's sentence, none ticked. The server decides what may be used: it must be a secure address on the public internet, never this machine, a private network, the cloud metadata address, or a name that points to one, and the box shows what the server says in its own words (for example *…is a loopback address… Nothing was saved.*, or *events is a list…* when no kind is chosen). **Add the webhook** makes it.
- **The secret.** When a webhook is made, or its secret is replaced, a box shows its signing secret once, in a field with a **Copy** button: *This is the only time the secret is shown. Put it in the place that receives the messages.* Close it and the board has forgotten it: it is not on the page, in the address, in a list or in what the server tells you later. Put it where the receiver can read it, because it is what checks a message.
- **Test** sends one message of the kind *ping* to the address now, once, and says in the row for a few seconds how it went, in the server's words: *Delivered: The receiver answered 200.* or *Not delivered: The receiver answered 500.* It can take up to ten seconds, and the button waits (*Testing…*) until it is over. A test counts like any other attempt, so a webhook whose test fails shows **Failing**.
- **Change kinds…** opens the box with the current kinds ticked; **Save** keeps the choice, and a choice of none is refused in the box.
- **Turn off** and **Turn on**. Nothing is sent to a webhook that is off, and what happened while it was off is not sent when it is turned on.
- **Replace secret…** says in one sentence what it does (Vrum makes a new secret, shows it once, and the old one keeps signing too for a day so that the receiver can change over without missing a message) and then shows the new secret.
- **Messages** opens what was sent to it, newest first: the kind, how it stands in words (*Delivered*, *Waiting for the next try at …*, *Given up on after 6 tries*), how many tries, the receiver's answer, what went wrong and when. A message that does not get a good answer is sent again after 10 seconds, 1 minute, 5 minutes, 30 minutes and 2 hours, and then given up on.
- **Delete…** asks first, then removes the webhook and forgets the messages still waiting.

## Email nudges

On the **Account** page, below the sign-in methods, **Email nudges** is where you ask to be emailed when something waits for you. For each project you are in, whatever your role (a viewer too), there is a row with the project's name and five kinds of news as boxes, each with the server's sentence: a question or an approval put to a person, a drafted plan waiting for approval, a proposal to move work, a story or task that was blocked, and a claim that ran out. Tick the ones you want and press **Save** on that row: nothing is saved by ticking, and each project has its own **Save**. It says *Saved. You will be emailed about 2 kinds of news in this project.*, or, when you ticked none, *Saved. You will not be emailed about this project.* which stops them. A refusal is shown under the row in the server's words.

The mail says what kind of news it is and which story or task, with a link to the board, and nothing anyone wrote. It goes only to you, at most ten an hour, and stops if you leave the project. It needs the server to be able to send email (see [Email](#email)): without it the section says in one sentence that email is not set up on this server and has no controls. It also needs your address to be confirmed, so that nobody can aim a message at someone else's address: with an unconfirmed address the section says *Confirm your email address first.*, with a button to the **Email** setting above where **Send verification email** is, and the boxes cannot be used until you have.

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
   password, then connect the provider from the Account page.
5. If there is no account with that address, Vrum makes one (unless the server is set not to): its
   name is the one the provider gives for you, or the part of your address before the @ sign, the
   address counts as confirmed, and it has no password.

**An account with no password.** An account made by a provider has no password, so email and
password cannot sign in to it until you set one. The sign-in page gives the same answer as for a
wrong password. Account page, Sign-in methods, **Set a password…** sets a first one (it asks only for
the new password, twice), and **Forgot your password?** does the same when the server can send
email.

### Connecting and disconnecting

The Account page shows **Sign-in methods** (instead of **Password**) when the server
offers another way to sign in or your account has no password. It lists the password and each way
the server offers, one line each:

- **Password.** With a password it is the word and **Change password…**. With no
  password it says *You have no password. Sign in with <names>. Set a password to also sign in with
  email.*, with **Set a password…**.
- **A provider that is connected** says *Connected as <the provider's name for you>*, with the
  provider's address for you and when it was connected, and **Disconnect…**. Disconnecting asks to
  confirm and changes nothing else: your account, your projects and everything you recorded stay.
  It also ends your other sessions; the one you are in stays.
- **A provider that is not connected** has only **Connect <name>**. OpenID
  Connect and GitHub take you to the provider and back to the Account page, where a notice says the
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
shows as a notice on the Account page. `<name>` below is the provider's name on its button.

| The page says | What it means |
| --- | --- |
| *Signing in with <name> was cancelled. Nothing was changed.* | You, or the provider, stopped the sign-in. Start again if you want to. |
| *Signing in with <name> did not work. Nothing was changed. Try again, and if it keeps happening ask whoever runs this server.* | The provider's answer could not be trusted or used (for example it was not signed properly). The words never include what the provider said, so nothing from it can reach the page. The server's log holds the reason without any secret. |
| *That sign-in took too long or was already used. Start again.* | A sign-in is good for ten minutes and once, in the browser that started it. Reloading the way back, opening it in another browser or restarting the server ends it. |
| *Vrum could not reach <name>. Try again in a moment.* | The server could not talk to the provider. |
| *<name> did not give a confirmed email address, so Vrum cannot tell who you are. Confirm your email address there, or sign in another way.* | The provider gave no address, or did not say it is confirmed. |
| *Accounts with that email address may not sign in this way on this server.* | The server only lets some email domains use this way in. |
| *An account with your email address already exists here, but its address was never confirmed, so Vrum will not join the two. Sign in with its password, then connect <name> from the Account page.* | See step 4 above. |
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

The **Settings and tools** menu in the sidebar holds five pages.

**Users.** Who is in the project and how each shows in its history: the members with
their roles and the invitations, and the agents with their keys (see
[Users and identities](#users-and-identities)). Your own account (name, email, password and ways to sign in) is on the **Account** page, which the account menu opens, not in this menu. An old address,
`#/access`, is no longer a page and opens the Board.

**Definition of done.** The project's one definition of done (see [Definition of done](#definition-of-done)).

**Plan templates.** A plan template is a plan you can make again: epics, stories and tasks, with the links and acceptance criteria between them, and **variables** (such as a version or a station's name) that you fill in each time. A template is saved as data, never code: its text holds `{{name}}` wherever a variable's value goes, and each variable is a line of text (optionally one of a list of choices, or limited by a length or a simple pattern) or an epic or story that exists. Making a plan from a template gives exactly the plan that writing it out by hand would, with the same checks, in one step. The page (`#/templates`) lists the project's templates, read from the server as an agent reads them:
each template's name, its version (how many times it has been saved), its description, what it makes (*1 epic, 2 stories and 4 tasks*), its variables as small chips (rest the pointer on one for what it is for and what it takes), and who saved it and when. With no template the page says in one sentence what a template is.

- **Make a plan…** opens a box with one labelled field for each variable: a text field, a list for a variable with choices, or a list of the project's epics or stories for a reference. Each says in words whether it is **required** or **optional**, and a default is already filled in. An optional **plan title** and a box, **Wait for a person to approve the plan**, make the plan a draft: its work is held until a person approves it on the Needs a person page. When the values do not fit, the box lists every problem the server finds, in its own words. When the plan is made, the box says what it made, links the epics and stories, and, for a draft, says *Plan 3 waits for a person to approve it* with a link to the Needs a person page.
- **View** reads the whole template as text, as the server keeps it. A viewer can do that and nothing else here.
- **Change…** reads the whole template into a box that holds its name and its text as JSON; **Save the template** saves it under the name, and saving a name again makes a new version (every version stays in the history). **New template…**, in the top right, opens the same box empty. The page only reads that the text is JSON: the server checks the template whole (its shape, its variables and the plan it makes) and the box lists every problem, one after the other, with nothing saved.
- **Delete…** asks first. Plans already made from the template stay as they are, and every version of it stays in the history.

**Tools.** One page for the one MCP server: every tool the server has, read from the server itself so the list can
never be out of date; a header link, **Agent keys**, goes to the Users page, where an agent is connected. A switch, **All tools**,
**Offered to agents**, **Board and administration only** and **For keys that run code** (each with its count), lists
one kind alone; it is kept in the address (`show=offered`, `show=board` or `show=code`) and works with the search,
which also says how many tools it shows. The last switch is there only when the server has such tools: `explore` and `run`,
the two tools of a key that runs code, wear the chip **For keys that run code** and are in neither of the other counts.
`explore` has **Try it** (it lists what a key that runs code can do, and gives the types of the functions, for the standard
set, since a person has no key); `run` has none, because it changes something.
The tools are grouped only to make them easier to find, and inside each group the ones that only
read come first and the ones that change something after. For each tool: its description, its
fields, whether it changes anything, a quiet chip that says **Offered to agents** or **Board and
administration only**, and where on the board it can be done, or that it has no page there and
why. A tool only people can use says so. Any tool that only reads has **Try it**, which fills in a
form from the tool's fields; **Run** shows what the server answers (it is the board that asks, as the
person signed in, so Try it works for a tool that is not offered to agents too). A tool that
changes something has no Try it, and the server refuses one even if it is asked.

**Status and export.** What a person needs first: *The server is running and can reach its database*, whether search by
meaning is in use (or not set up, or not answering, or how many items wait to be indexed) and whether the server can send email
(*Set up*, or *Not set up: invitations are shared by link and passwords cannot be reset by email*), then **Download export**, which saves
the whole graph of this project as one JSON file, identical to the one `export_graph` gives, with
nothing of any account in it. Backup, restore and import stay on the command line (see
[backup and export](backup-and-export.md)), and the page says so. A closed **Technical details** section holds the rest: the version of Vrum,
PostgreSQL and pgvector, the state of the search index (which embedding service and model it uses, the size of the vectors, how many items
are indexed and the last reindex since the server started), the mail server (*Sending as <name and address> through <host>*, never the user name
or password), when the server offers another way to sign in a **Sign-in** group (each one's button name, what kind it is and the address it talks to,
never a client secret or a password, which email domains may use it, and whether a first sign-in may make a new account) and **How much it holds**, how many of each kind of item
there are. It is what `server_status` says, for this project, and the email line. No password, session or key and
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
  or `none`. `is:` finds `overdue`, `conflict` (planned to start too early), `waiting`, `claimed`,
  `lapsed` (a claim that ran out), `held` (kept back by a gate or by a plan that waits for approval, see [Gates and held work](#gates-and-held-work)) and `unverified`: work that is blocked, that nobody holds and that has a
  check only a verifier (or a person) may tick, still unticked, which is what is waiting for a verifier
  (`is:unverified`, or `is:unverified epic:3` for one epic).
- `OR` between groups: `kind:task status:ready -claimed priority:high OR kind:story`. Terms next to
  each other must all hold. `OR` (in capitals) starts a new group.

A query that cannot be read is refused with a plain message saying what was not understood, and
nothing is listed. The filter is kept in the address, so you can share a view by copying the link.

## Addresses

Everything you choose lives in the address after the `#` (never a session or a key), so a view can
be reproduced by opening the same address, and the server keeps no saved views.

- The page is `#/board`, `#/graph`, `#/roadmap`, `#/lists`, `#/search`, `#/activity`, `#/needs` or `#/coverage` (or
  `#/users`, `#/definition`, `#/tools`, `#/status`, and `#/account` for your own account).
- After it come the choices: `q=` for the work filter and `epic=` (the epic's number) for an epic.
  The Board also has `show=` (`tasks`, `stories` or `both`; no `show` is `tasks`).
- A record's own page is `#/item?item=story:12` (or `epic:3`, `task:40`, `rule:3`, `note:7`),
  with `from=board` (or `graph`, `roadmap`, `lists`, `search`, `activity`, `needs`, `coverage`) for
  the view it was reached from. The review page of a change is `#/review?change=plan:3`, with
  `revision=2`, `mode=whole`, `view=split` and `group=topic` as choices (see
  [Needs a person](#needs-a-person)); the list takes `group=level`, `topic` or `decision`.
- For the Graph: `tab=structure`, `around` or `order` (Structure opens when there is none or the
  tab is not one of these, so its address has no `tab`; an older `tab=timeline` opens Structure), and
  `focus=` with a record, `story:12` for instance (no `focus` means the whole project). Each tab
  keeps its own choices: `scope=`, `level=`, `labels=`, `colour=` and `links=1` for Structure;
  `reach=` and `wide=1` for Around; `mode=chains`, `phase=`, `chain=`, `budget=`, `blocked=1` and
  `pick=` for Order.
- For Lists: `view=work`, `requirements` (the acceptance criteria) or `notes` (an old `everything` opens the work list), with `q=` and `epic=`.
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
