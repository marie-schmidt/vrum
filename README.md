# Vrum

<p align="center">
  <img src="docs/marketing/website-header.png" alt="Vrum. Keep building. Keep the context. Give your coding agents shared tasks, lasting decisions and clear handoffs." width="720">
</p>

**Keep building. Keep the context.**

Give your coding agents shared tasks, lasting decisions and clear handoffs. Spend less time
repeating yourself and more time moving the project forward.

Vrum is a project graph that you run yourself, for AI coding agents and the people who work
with them. It keeps what a project is made of: epics that hold stories that hold tasks, the
requirements each story must meet, the decisions people made and why, and the facts the team has
learned and where they came from. Agents use it over MCP, so that every agent, in every session,
starts from what is already known instead of from nothing. People see all of it, and steer it, on a
board in the browser. It is built to be effective and efficient for agents and friendly to people:
every refusal says what is wrong and what to do next, and every answer is small enough to read.
Vrum stores, retrieves and coordinates; it never generates answers or conclusions: the agents
connected to it do the reasoning and record what they find.

It is a prototype, built one milestone at a time (see `PROGRESS.md`). You register an account on the
board, make private projects there, and give each agent a key for one project. You use a project
through an agent or a script, and through the board: **Board**, **Graph** and **Roadmap**, with
search, an activity drawer and a settings menu (agent access, the tools reference, status and
export) on every page.

## Quick start

You need Docker with Compose. Nothing else is needed to run it.

```
git clone https://github.com/marie-schmidt/vrum.git
cd vrum
./scripts/init-env.sh                  # writes .env with a random database password
docker compose up -d --build --wait    # builds the image and starts the database and the server
```

When that finishes, the server is healthy:

```
curl http://127.0.0.1:8000/health      # {"status":"ok"}
```

The board is at `http://127.0.0.1:8000/board` and the MCP endpoint is
`http://127.0.0.1:8000/mcp` (change the port with `VRUM_PORT` in `.env`). Open the board, **create an
account** with an email address and a password, **create a project**, and it opens. Nothing needs
to be typed in a terminal and no file needs to be read: there is no token to find. To let an agent
in, open the project's **Agent access** page and make it a key (see "Accounts, projects and agent
keys" and "Connect an agent").

- `docker compose down` stops it. Your data stays in a Docker volume and is there when you start
  again.
- `docker compose down --volumes` stops it and **deletes the data**, accounts included.
- `docker compose logs app` shows what the server did, as one JSON line per event. The logs never
  contain passwords, sessions, keys or anything you wrote.

To see for yourself that everything works, from a fresh clone, run `./acceptance.sh` (see
"Checks" below).

## Accounts, projects and agent keys

- **An account** is an email address and a password that Vrum keeps itself: the password only as a
  salted, slow hash (scrypt), never in plain text. Nothing is verified by email, and a forgotten
  password cannot be recovered yet (see `BLOCKED.md`: both wait for an answer from the owner, since
  Vrum has no way to send email). Several accounts may share one install.
- **Signing in** gives the browser a session: a cookie the page's scripts cannot read, that expires
  after 12 hours and ends the moment you sign out. Every change from the board also carries a
  secret that only the board's own page knows and is checked against where it came from, so another
  website cannot make your browser change anything. Failed sign-ins are slowed down: five wrong
  passwords for one address stop that address for ten minutes.
- **A project** is a private workspace: its own epics, stories and tasks, requirements, decisions,
  facts, claims, history and search. A person sees only the projects they
  made, may make as many as they like, and switches between them from the top bar. Each project
  numbers its own items from 1, so node 12 in one project is not node 12 in another. Nothing is
  shared between projects, and a project can only be reached by its owner and by the agents the
  owner gave a key to. The database itself enforces this (every row belongs to one project and
  cannot refer to a row of another), not only the server.
- **An agent key** lets an agent use one project and no other. On the project's **Agent access**
  page you make a key (a label, such as the name of the agent), copy it once (only a hash of it is
  kept, so it cannot be shown again), and revoke it when it is not wanted. Revoking works on the
  agent's next request, even on a connection that is already open. An agent's name is only what its
  changes are recorded with: it proves nothing and opens nothing.
- **Admins and backups** stay outside accounts: `./scripts/backup.sh` and `./scripts/restore.sh`
  work on the whole database, which holds everything of everyone, so keep them and `.env` where only
  you can read them.
- The server answers only to requests addressed to `localhost`, and compose publishes it on
  `127.0.0.1` only. That is right for one machine. Passwords and keys travel as plain text over
  HTTP: to use it from another machine, put it behind something that adds TLS (the session cookie
  is then marked Secure), and read `DECISIONS.md` (the M6 and M15 sections) first.

## Connect an agent

Any MCP client that speaks streamable HTTP can connect. It needs the address and the **agent key**
of a project: open the project's board, choose **Agent access**, make a key and copy it. A key
reaches that project and no other.

**Claude Code** (it keeps the key in its own settings, so do this where only you can read them):

```
export VRUM_AGENT_KEY=<the key from the Agent access page>
claude mcp add --transport http vrum http://127.0.0.1:8000/mcp --header "Authorization: Bearer $VRUM_AGENT_KEY"
```

**A project file** that any of the clients below can read, as `.mcp.json` at the root of the
project. It holds no key: each client reads `VRUM_AGENT_KEY` from the environment.

```json
{
  "mcpServers": {
    "vrum": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer ${VRUM_AGENT_KEY}" }
    }
  }
}
```

**GitHub Copilot** (the CLI and VS Code) reads the same `mcpServers` shape from
`~/.copilot/mcp-config.json` (for all your projects) or from `.mcp.json` in the workspace, with an
added list of the tools to allow:

```json
{
  "mcpServers": {
    "vrum": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": { "Authorization": "Bearer ${VRUM_AGENT_KEY}" },
      "tools": ["*"]
    }
  }
}
```

**From Python**, with the official MCP SDK (this is what the scripts in `scripts/` and the checks
use; see `scripts/vrum_client.py`):

```python
import asyncio, os
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client


async def main() -> None:
    headers = {"Authorization": f"Bearer {os.environ['VRUM_AGENT_KEY']}"}
    async with (
        create_mcp_http_client(headers=headers) as http,
        Client(streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http)) as client,
    ):
        print(await client.call_tool("server_status", {}))


asyncio.run(main())
```

The Python example is run against the real server by `./acceptance.sh`, so it works. The other
three follow each client's own documentation for remote HTTP servers (Claude Code: "Connect Claude
Code to tools via MCP"; Copilot and VS Code: "MCP configuration reference"); they were not run
here. If a client does not fill in `${VRUM_AGENT_KEY}` for you, put the key in that client's own
user-level settings rather than in a file you commit. If a client says it cannot connect, the
server's answer says why: a missing, wrong or revoked key is refused in plain words.

## The agent guide

This is the text the server hands to every agent when it connects (its MCP instructions). It is
repeated here word for word, and a check fails if the two ever differ.

<!-- agent-guide:start -->
Vrum is a project graph. Epics hold stories, stories hold tasks, and requirements, decisions and facts are linked to them. A decision is what a named person decided; a fact is a statement with its source. Each is either unconfirmed or confirmed by a named person, and either in force or superseded or withdrawn: read both before you rely on it. Every write needs an actor: your own name.

The working loop:
1. `next_work` takes the most urgent free story or task and returns it with its requirements, decisions (project-wide ones included), facts, earlier handoff notes and similar items, all in one call. Keep the claim with `renew_claim` while you work.
2. Before you decide something that matters, `recall` what is known about the item. If you do not know where to look, use `search`.
3. Record what is decided as it happens with `record_decision` (name the person who decided in decided_by), and what you learn with `retain_fact`. What you record starts unconfirmed: only a named person's review makes it confirmed (`set_confirmation`).
4. End with `finish_work`: say whether it is done, blocked, dropped or released, and leave a handoff note for whoever comes next. A blank note is refused.

Also: `create_plan` creates a whole plan at once or nothing, `update_nodes` changes many nodes, `changes_since` shows only what is new since an event, and `ready_work` lists what is free. `list_nodes` takes a filter text as its query argument, such as `kind:task status:ready -claimed OR is:overdue`. [[node:12]] in any text refers to an item (also rule, decision, fact); `find_mentions` lists who refers to one. Lists leave out bodies unless you ask for detail. Refusals say what is wrong and what to do next. Anything holding a well-known secret format is refused in every field and never stored: leave secrets out of what you write. Vrum stores, retrieves and coordinates; it does not generate answers: you do the reasoning and record what you find.
<!-- agent-guide:end -->

Every tool describes itself: its description says what it does, what it needs and what it refuses,
and the client lists them all.

## The board

The board is for people. It is at `http://127.0.0.1:8000/board`, served by the same container and
on the same port as the MCP endpoint, and it shows and changes the very same graph: whatever an
agent does appears on it within a few seconds with no reload, and whatever you do on it is what an
agent reads next. It keeps no data of its own, and loads nothing from the internet.

**Signing in and your projects.** `/board` asks you to sign in or to create an account (a name, an
email address and a password of 10 characters or more). Every refusal says what is wrong, and a
wrong password or an address with no account gets the same answer. Once in, you see your own
projects, with an empty state and a box to create the first one when you have none; creating one
opens its board at `/board/p/<number>`. The top bar of every page says which project you are in
and switches to another (choosing one loads that project's board afresh, so nothing of the first
can appear in the second); **Sign out** ends the session on the server. A project's address copied
to another account opens nothing: it is refused in the same words as a project that does not
exist. Changes are recorded with your name, taken from your account when you sign in and
changeable with "You are …: change", next to the agents' names.

**Agent access** (Settings menu). The keys of the project: make one with a label, copy it (it is
shown once), see when each was made and last used, and revoke the ones that are not wanted. It also
shows the endpoint and how to send a key. See "Accounts, projects and agent keys".

**The top bar, three views and two drawers.** A project has three views, **Board**, **Graph** and
**Roadmap**, and the top bar of every one of them also holds the project switcher, a **search
box**, an **Activity** button, a **Settings** menu, a **Jump to** button, the name your changes are
recorded with, and, when the server cannot be reached, a banner that says so on whatever view you
are on. The board opens on the Board. Search and Activity open as a **drawer** on the left of
whichever view you are on; the **detail panel** on the right shows whichever item, requirement,
decision or fact you chose, on any view. Opening a drawer, switching between the Map and the List
of the Graph, or moving to another view never discards what you were typing in the detail panel or
in the drawer. Settings holds **Agent access**, **Tools** and **Status and export**.

**Board.** One column for each status and one lane for each epic. A card is a story or a task and
shows who holds it and until when, what it is waiting for, its due date (and whether it is overdue
or planned to start before something it needs is due), and its progress. Drag a card to another
column of its own epic to change its status, or use its **Move** menu from the keyboard. *Ready* is
worked out from the links, so nothing can be dropped there. Above the columns, **Needs attention**
counts the ready work nobody holds, the blocked and the overdue items, the claims that ran out with
no handoff note, the requirements that are in no story and the stories that have no requirement;
each number is a link that shows exactly those items. The **filter box** keeps only the cards the
filter language below asks for. **New item** makes an epic, a story or a task, and **Coverage**
shows the requirements that are in no story and the stories that have no requirement. Choose a card
to open it in the detail panel.

**Graph.** The project's work and context, as one scope shown two ways. Choose *what* to show:
**Work** (epics, stories and tasks, with the hierarchy and the links between them), **Context**
(requirements, decisions and facts, and the work they concern) or **Everything** (both). Choose
*how*: the **Map**, dots and lines with a different shape for each kind and a different line for
each kind of link, so colour is never the only clue, or the **List**, which says the same in text.
Both show the same records and the same relationships, and the record you chose and the epic you
are looking at stay when you switch. Pick one epic, or all of them: a project of more than a
thousand dots draws each epic as one dot and says so. **Reach** limits the map to what is one to
three steps around the chosen record. Drag a dot to pin it, zoom and pan, colour by kind, status or
epic, show replaced, withdrawn and dropped records, show the `[[references]]` written in texts,
and filter by kind, status and kind of line; the filter box takes the work filter language below.
A list of the chosen record's neighbours beside the map does everything a click on it does, from
the keyboard. Choose a dot or a row to open it in the detail panel.

**Requirements.** What a story must meet. In the Graph's List, in the Context or Everything preset,
every requirement is grouped by the story that owns it, with the requirements that are in no story
first (the newest on top) and the coverage report: the requirements in no story, and the stories
with no requirement, with dropped requirements and stories left out. Write one (**New
requirement**), change its title, details or status (**Edit**), and put it in a story: drag it
onto a story, or choose **Put in a story…**, which puts a requirement that is in no story into one
and takes one from its story to another in one step. A requirement is in at most one story, and
placing one that is already in a story is refused, in the server's words. (The tools and
`[[rule:3]]` references call a requirement a rule.)

**Facts and decisions.** What the project knows and what was decided, in the same List, newest
first. A **decision** is what a named person decided and why (its title, text,
reason and who decided it), and is marked **project-wide** when it applies throughout the project.
A **fact** is a statement with where it came from (its source) and who recorded it. Each row says
what it is, whether a person has confirmed it, and whether it is still in force. Filter each
list by status (in force by default, replaced, withdrawn or all), by whether a person confirmed
it, and by words; a list shows the first hundred and offers more. **Record a decision** and
**Record a fact** write one, and each can name the items it concerns (picked from a list). Neither
is ever edited: **Replace** records a newer one and keeps the old one as a record, pointing at its
replacement, and **Withdraw** gives one up with a reason; the form starts from the old words, and
a replaced record shows the chain. **Link** changes what a record in force concerns. Choose a row
to open it in the detail panel, where its confirmation, lifecycle, links, mentions and history are
written out.

**Confirmed or not.** Every fact and decision has an **Unconfirmed / Confirmed** dropdown. A
record starts **Unconfirmed**, whoever writes it, and an agent that records something never
confirms it. Choose **Confirmed** and the board asks who reviewed it: the name goes on the record
with the time, and your own name is recorded separately as the one who made the change. It means
that a named person looked at the record, not that Vrum checked it is true. Choosing
**Unconfirmed** again asks who says so and what they said, because it takes away what a person
gave. Only a record in force can change its confirmation: what a record was confirmed as stays on
it, and in its history, after it is replaced or withdrawn. Replacing or withdrawing a record that a
person confirmed also asks who changed their mind and what they said, and both the person and you
are in the history. Recall and the briefing an agent gets show every record's confirmation and
whether it is in force, so an unconfirmed record is never presented as confirmed.

**The detail panel.** Choose an epic, story, task, requirement, decision or fact, from any view,
a search result, the activity feed or a `[[reference]]`, and it opens on the right. An epic, story
or task shows its last handoff note, its details and the form to change its title, details,
priority, dates and parent (at the version you saw), the links to add or remove, its requirements,
the **facts and decisions** that concern it as an agent's `recall` would say them (decisions in
force, project-wide decisions, facts, and the replaced or withdrawn ones apart, each with its
confirmation and why it is there), what mentions it, what the index thinks it may duplicate, and
its history. A requirement, decision or fact shows all its fields, its confirmation and
lifecycle, what it concerns, what mentions it and its history, with the buttons above. If an agent
changes what you have open while you are editing it, the panel says so and offers to load the
current version, and what you typed stays.

**Roadmap.** The work on a timeline, each item a bar from its start date to its target date, with
a line for today and zoom by month, quarter or year. Group by epic, status, priority or who holds
it, and filter with the filter language below. Drag a bar to move both dates, or one end to move that
date; with a bar focused, the arrow keys move it a day (Shift for a week, Alt for the start only,
Ctrl for the target only), and Space picks several bars to move together. A pale dashed bar is an
epic or story with no dates of its own, drawn over the span of what is under it. An arrow runs from
what must come first to the item that waits for it; an item planned to start before something it
needs first is due is marked "Schedule conflict" (a warning, nothing is blocked), and an overdue
item says so in words. Items with no dates wait in a tray above: drag one onto the timeline to give
it a week starting where you drop it, or type its dates there. Every move is a change at the
version the bar was drawn at, so one an agent changed since is refused and the bar goes back, and
a start after the target is refused in the server's words. Choose a bar to open its item in the
detail panel. The roadmap has no sprints.

**Search.** The box in the top bar opens the search drawer, on any view, over the `search` tool.
Each result says how it matched (exact words, meaning or both), why, shows a snippet with the
matched words marked, and its parent and status as the graph holds them now; choose one to open
it in the detail panel, and the drawer stays open. Choose *Exact words only* or *Meaning only* to
see one kind of match. The counts by kind (epic, story, task, requirement, decision, fact), by
status and by epic filter the results and keep the server's order. The drawer says how many items
are waiting to be indexed: results by meaning may lag behind them, results by exact words never
do. With the embedding service stopped, search by words still works, and searching by meaning
says why it cannot. The search and its filters are in the address, so a search can be shared.

**Possible duplicates.** The second tab of the search drawer: pairs of stories (or epics, or
requirements) in different epics that look like the same thing, most alike first, with their
scores, side by side with their details. Change how alike they must be (the server's setting is the
start), or name epics to look at only pairs that have something in them. **Mark as duplicates** and
**Mark as related** make that link in one click, with your name, and nothing is ever linked unless
you click: looking never writes. Choose to compare stories, epics or requirements (requirements are
compared too, and cannot be linked). It needs the embedding service.

**Markdown and references.** Details, requirements, reasons, decision texts and facts are shown
as Markdown by the board's own small renderer: headings, paragraphs, lists, emphasis, code, quotes
and web addresses, which open in a new tab. Raw HTML is never rendered and nothing is loaded from
elsewhere, so a script or image tag is shown as the text it is. Write `[[node:12]]`, `[[rule:3]]`,
`[[decision:7]]` or `[[fact:5]]` in any text to refer to an item: it is shown as a link with the
item's current title (and follows a rename), or plainly as missing when nothing has that id, and
it opens the item in the detail panel. Typing `[[` in an editor offers a pick-list. A reference is
only text: it makes no link
and changes no readiness and no order. Every item shows **Mentioned in**, the items whose text
refers to it (the `find_mentions` tool works this out when asked and stores nothing), and the
graph has a **References** switch that draws them. Titles are not read for references.

**Jump to.** Press Ctrl+K (⌘K on a Mac), or use the button in the top bar, on any view: type a
word of an item's title, its id (`12`, `#12`, `requirement 3`, `decision 7`, `fact 5`) or the name of a
view, drawer or settings page, and Enter goes there.

**Activity.** The **Activity** button in the top bar opens a drawer, on any view, with what
happened, newest first, in plain words ("Ada moved rule 7 from story #3 to story #5"), with a
handoff note shown in full. New changes appear within seconds with no reload. Filter by who, by
kind of change (work, links, claims, handoffs, requirements, decisions, facts), by item and by day
(UTC); the filter is in the address. Above the feed, **Who is working on what** shows every claim
in force with a countdown and every claim that ran out, and each agent's state (working, lapsed,
finished). You can claim an item under your typed name
(by its number, or from the work that is free to take), renew a claim, or release it with a note:
the same operations an agent calls. A claim that ends moves to *lapsed* by itself. An event in the
feed opens its item in the detail panel.

**Tools** (Settings menu). Every tool the server has, read from the server itself so the list can never be out of
date: its description, its fields, whether it changes anything, and where on the board it can be
done, or why it is for agents only. Any tool that only reads has **Try it**, which fills in a form
from the tool's fields and shows what the server answers. A tool that changes something has no
Try it, and the server refuses one even if it is asked. It also shows the endpoint, and how to send
an agent key with an example connection.

**Status and export** (Settings menu). The version of Vrum, PostgreSQL and pgvector, how many of each kind of item there are,
the state of the search index (how many wait to be indexed, which embedding service and model it
uses, whether the service answers, the size of the vectors and the last reindex since the server
started). It is what `server_status` says, for this
project. **Download export** saves the whole graph of this project as one JSON file, identical to
the one `export_graph` gives, with nothing of any account in it. Backup, restore and import stay on
the command line (see below), and the page says so. No password, session or key and not the
database password is ever shown anywhere.

**The filter language.** One way to say which items you mean, for the filter box of the Board, the
Roadmap and the Graph's work and for the agents' `list_nodes` tool (as `query`), with the same
answer for all of them.

- Words find items whose title or details contain them, whatever the case: `login "reset password"`.
- `field:value` narrows by a field, and commas mean "any of these": `status:ready,in_progress`.
  The fields are `kind`, `status`, `priority`, `claimed`, `parent`, `epic`, `story`, `id`,
  `start`, `target`, `is` and `no`.
- A leading `-` leaves things out: `-status:done`, `-claimed` (not held by anyone).
- `claimed` and `waiting` alone mean held by someone and waiting on something; `no:field` means
  empty (`no:start`, `no:target`, `no:claimed`, `no:parent`, `no:body`, `no:waiting`).
- `start:` and `target:` take a day, a range (`2026-11-01..2026-12-31`, either end may be left
  open) or `none`; `is:` finds `overdue`, `conflict` (planned to start too early), `waiting`,
  `claimed` and `lapsed` (a claim that ran out).
- `OR` between groups: `kind:task status:ready -claimed priority:high OR kind:story`. Terms next
  to each other must all hold; `OR` (in capitals) starts a new group.

A query that cannot be read is refused with a plain message saying what was not understood, and
nothing is listed. The filter is kept in the address, so you can share a view by copying the link.

**Addresses.** Everything you choose lives in the address after the `#` (never a session or a key),
so a view can be reproduced by opening the same address, and the server keeps no saved views. The
view is `#/board`, `#/graph` or `#/roadmap` (or `#/access`, `#/tools`, `#/status`), and after it
come the choices: `sel=node:12` (or `rule:3`, `decision:7`, `fact:5`) for the record open in the
detail panel; `q=` for the work filter and `epic=` for an epic; for the Graph, `view=work`,
`context` or `everything` and `as=map` or `list`; for the Roadmap, `group=` and `zoom=`;
`drawer=search` or `drawer=activity` for the drawer, with `search=`, `search_mode=` and the
other `search_…` choices, `dups=1` for possible duplicates, and `act_…` choices for the
activity filters. For example `#/graph?view=context&as=list&sel=fact:5`.

**It is the same graph.** A change on the board is the same operation an agent calls, with the same
checks. If you drop a card where it cannot go, or edit something an agent has changed since you
read it, the board says what the server says, in the same words, and keeps what you typed. How
often the board asks for news is `VRUM_BOARD_POLL_SECONDS` (5 by default).

## What is in it

- **Work:** epics, stories and tasks, in an exact hierarchy, with priority, status, a start and a
  target date, and links between them (`needs_first`, `supersedes`, `duplicates`, `relates_to`).
  What is ready to start is worked out, never typed in. Work is claimed with a lease, so two agents
  never take the same task, and a handoff note says what the next one must know.
- **Requirements:** each requirement sits in exactly one story, and a report lists the ones with no
  story and the stories with no requirements.
- **Decisions and facts:** the two kinds of context a project keeps. A decision is what a named
  person decided and why; one that applies throughout the project is marked project-wide and is
  included in every briefing and recall of that project. A fact is a statement with where it came
  from. Neither is ever edited: a change is a newer record that replaces the old one, or a
  withdrawal with its reason, and the old one stays as a record. Both start **unconfirmed**; a named
  person can confirm one (who and when are kept), which says that someone reviewed it, not that it
  is true. An agent that records a statement is never thereby a person's confirmation, and changing
  a confirmed record needs that person's word. What a record was confirmed as stays in its history
  after it is replaced or withdrawn.
- **Search:** by exact words, by meaning, or both, and each result says how it matched. It also
  finds similar items and stories that overlap across epics. Any text can refer to an item with
  `[[node:12]]`, `[[rule:3]]`, `[[decision:7]]` or `[[fact:5]]`, and `find_mentions` says what refers
  to what.
- **History:** every change is kept, with who made it and what it was before. `changes_since`
  gives an agent only what is new.

## Optional: finding by meaning

Searching by exact words needs nothing. Searching by meaning, similar items and possible duplicates
need an embedding service: any OpenAI-compatible one, set in `.env` (see `.env.example` for every
setting). Without it everything else works, and a search says plainly that meaning is not set up.
Vrum never asks a language model anything: the embedding service only turns text into numbers so
that records can be compared.

To run an embedding model on this machine, add `EMBEDDING_BASE_URL=http://ollama:11434/v1` and
`EMBEDDING_MODEL=all-minilm`, and start with
`docker compose --profile local-embeddings up -d --build --wait` (the first start downloads about
50 MB). Nothing is sent anywhere unless you set a URL, and text that holds a well-known secret
format (an access token, a private key) is never stored.

## Backup, restore and moving data

- **Backup:** `./scripts/backup.sh` writes the whole database to one file (by default in
  `backups/`, which is not committed), while the server keeps running. It holds everything of every
  account and project, password hashes and keys' hashes included, so it is not an ordinary
  account's to take: it is for whoever runs the server. The file is readable only by you and is not
  encrypted, so keep it where only you can read it.
- **Restore:** `./scripts/restore.sh <file>` puts it back, accounts and projects with it. Into a
  fresh database (after `docker compose down --volumes`) it just restores. Into a database that
  holds data it refuses, unless you add `--replace`. It restores into a new database first and
  swaps it in only when that worked, so a bad backup never costs what is there.
- **Export and import of one project:** with the agent key of the project in `VRUM_AGENT_KEY`,
  `uv run python scripts/export_graph.py http://127.0.0.1:8000/mcp graph.json` writes the whole
  graph of that project as one JSON file that you can read and keep, and `scripts/import_graph.py`
  loads such a file into an **empty project** (make one on the board, and a key for it), keeping
  every id, date, confirmation and the history. It is all or nothing, and the file can be as big as one
  request may be (4 MiB unless you raise `VRUM_MAX_REQUEST_BYTES`). Accounts, sessions and keys are
  never in the file.
- **Markdown copy:** `uv run python scripts/export_markdown.py http://127.0.0.1:8000/mcp notes`
  writes one file for every epic, story, task, requirement, decision and fact of the project, with
  its fields (including whether a person confirmed it, and whether it is still in force) at the top and everything it is connected to as `[[references]]`, to read in Obsidian
  or keep in git. It is read-only and is made again from the graph each time.

These scripts need `uv` and `VRUM_AGENT_KEY` in the environment (`export VRUM_AGENT_KEY=<the key>`).

## Settings

Everything is set in `.env`, which `docker compose` reads. `.env.example` lists every setting with
what it does. The ones you are likely to want:

| Setting | What it does |
| --- | --- |
| `VRUM_DB_PASSWORD` | The database password. Required. `./scripts/init-env.sh` makes one. |
| `VRUM_PORT` | The port on this machine. 8000 by default. |
| `VRUM_BIND_ADDRESS` | The address the port is published on. `127.0.0.1` by default: this machine only. |
| `VRUM_MAX_REQUEST_BYTES` | The most one request may hold. 4 MiB by default. |
| `VRUM_BOARD_POLL_SECONDS` | How often the board asks the server what changed. 5 by default. |
| `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL` | An embedding service, for search by meaning. |

## Checks

You need Docker with Compose, `uv` and `openssl`. Neither check touches your own stack or your
`.env`: each builds its own throwaway stack with a random database password and removes it afterwards.

- `./check.sh` is for developers, before a commit: lint, strict types, unit tests, and hundreds of
  integration tests against real containers, with a real MCP client and a real headless browser
  for the board. The browser is Playwright's Chromium, a development tool that is never in the
  image; `uv` downloads it the first time (about 100 MB).
- `./acceptance.sh` is the shorter story to read and trust. From a clean clone it starts Vrum,
  and walks one scenario through a real MCP client and a headless browser: it registers an account,
  creates a project and an agent key, and is turned away without one, plans, works with ten agents
  at once, records decisions and facts with their sources and reasons, confirms them and replaces
  and withdraws them, finds (by exact words, and by meaning with the embedding service stopped and
  back), is careful with secrets, and shows the project to a person (what agents do appears on the
  Board, what a person does is in the graph, refusals read the same; the Graph's work, context and
  everything presets and its map and list; the Roadmap, the filter language, dragging bars and
  an address that reproduces a view; search and possible duplicates in the drawer, and what a
  click on a pair creates; Markdown that is only ever text, references and their backlinks and the
  quick switcher; the Activity drawer with an agent's work and a person's claim; the Tools and
  Status pages against the server's own list and `server_status`, and the export), and finally
  backs up, destroys its own database, restores it, and loads an export into a fresh server.
  Every line it prints is one proof that holds. It uses a scripted stand-in for the embedding
  service, so nothing is downloaded and the answers are the same every run.
  `./acceptance.sh finding board` runs only those chapters.

## Where things are

- `SPEC.md` says what Vrum is, milestone by milestone, with the proof each one has to pass.
- `PROGRESS.md` says how far it has got, with the proof as it was run.
- `DECISIONS.md` lists every choice the specification left open, so that you can overrule any.
- `BLOCKED.md` lists the questions only the owner can answer (email verification, account recovery).
- `AGENTS.md` tells whoever works on the code how to run it and where things are.
- `src/vrum/board/` holds the board: its server side and its page files (plain HTML, CSS and
  JavaScript, with no build step), and the logo and icons in `src/vrum/board/static/brand/`.
- `docs/marketing/` holds the website header and the social sharing image.
