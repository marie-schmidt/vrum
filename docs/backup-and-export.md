# Backup, restore and export

Vrum keeps everything in one PostgreSQL database, in a Docker volume. There are two kinds of copy:
a **backup** of the whole database, for the person who runs the server, and an **export** of one
project, as data you can read, keep or load into another project.

`docker compose down` stops the stack and keeps the data. **`docker compose down --volumes` stops
it and deletes the data, accounts included.** Make a backup or an export first if you need it.

## Backup and restore of the whole database

- **Backup:** `./scripts/backup.sh` writes the whole database to one file, by default in `backups/`
  (which is not committed), while the server keeps running. It holds everything of every account and
  project, password hashes and keys' hashes included, and who is in which project and the hashes of
  its invitations, so it is not an ordinary account's to take: it is for whoever runs the server.
  The file is readable only by you and is **not encrypted**, so keep it where only you can read it.
  Give a file name to choose where it goes: `./scripts/backup.sh my-backup.dump`.
- **Restore:** `./scripts/restore.sh <file>` puts it back, accounts and projects (and their people)
  with it. Into a fresh database (after `docker compose down --volumes`) it just restores.
- **`--replace` deletes what is there.** Into a database that holds data, `./scripts/restore.sh` refuses
  unless you add `--replace`, which throws that data away and replaces it with the backup. It
  restores into a new database first and swaps it in only when that worked, so a backup that cannot
  be restored never costs what is there. The server is stopped while it runs and started again at
  the end.
- The checkout must have the same version of the database schema as the one that made the backup,
  or the server refuses to start and says so. Vrum has no migrations.

## Export and import of one project

These scripts work on one project, with its agent key in `VRUM_AGENT_KEY`. They need `uv` and a
checkout of this repository, and nothing else to be installed. The key is the project's, so make
one on its **Users** page (the **Agents** section) first (see [connecting an agent](agents.md)).
**Give it the tools these scripts use:** moving data is not one of the tools Vrum offers agents
by default, so choose **Everything, including administration** when making the key, or tick
`export_graph` (export and the Markdown copy) and `import_graph` (import) under **Not offered to
agents by default**. Without them the script is told that the tool is done on the board, by a
person, and nothing is changed.

```
export VRUM_AGENT_KEY=<the key of the project>
uv run python scripts/export_graph.py http://127.0.0.1:8000/mcp graph.json
```

- **Export:** `scripts/export_graph.py` writes the whole graph of that project as one JSON file that
  you can read and keep: every item with its number or id, dates, statuses and history, with
  who made each note and each change and as what (a person or an agent). An
  epic, story or task is in it by its kind and number (`story:12`), and everything that points at
  one (a parent, a link, the story or task an acceptance criterion is on, what a note concerns, an
  event) names it that way. The size of each story and task and the ticks of the definition of
  done's checks (who ticked each, when, and how it was checked) are in it, and so is the
  project's definition of done itself, with its version and who
  last changed it. The file is readable only by you and holds everything in the project, so keep it
  private. Accounts, sessions, keys, people and invitations are never in it. The board's **Status
  and export** page has the same download. Gates, drafted plans and their decisions (with every
  revision of a plan and the feedback people gave on it, including the lines they approved), saved plan
  templates, questions and their answers, and proposals to move work (including the items a
  person left out when accepting one in part) are all in it as part of the
  history, so they come back as they were (a timer gate still holds its task, a drafted plan is
  still held and a person can still decide it).
- **Import:** `scripts/import_graph.py` loads such a file into an **empty project** (make one on the
  board, and a key for it), keeping every number, id, date, status and the history, so
  `story:12` is still `story:12`, and the next new epic, story or task continues after the highest
  number of its own kind. It is all or nothing, and the file can be as big as one request may be
  (4 MiB unless you raise `VRUM_MAX_REQUEST_BYTES`). A project that already holds anything
  refuses. To move a bigger graph, back it up and restore it.
- **A file made before sizes and checks.** The current file is version 6. A file of version 5 has
  no sizes and no ticks of checks: it is still loaded, with no size on any story or task and
  nothing ticked, and the checklist comes from the definition of done it holds.
- **A file made before acceptance criteria could be on a task.** In
  a file of version 4 or earlier an acceptance criterion was in a story, and
  the file says `story` where version 5 and later say `node` (a story or a task): it is still loaded, with
  each criterion on the story it was in, and the history says `node` too. Such a file has no
  definition of done, so the project starts with an empty one.
- **A file made before notes had one status.** In a file of version
  3 a note was `active` (with who confirmed it kept apart) and an acceptance criterion was held or
  settled. It is still loaded: an active note that a person had confirmed becomes a confirmed note and any
  other active note an unconfirmed one, a note that was replaced or withdrawn stays so and keeps
  who had confirmed it, and a held criterion becomes open and a settled one met. The history
  says the same words.
- **A file made before notes.** Until version 3 a project kept decisions and facts as two kinds of
  record, and a file written then (format version 1 or 2) is still loaded. Its decisions become
  notes first, in the order of their ids, and its facts after them, numbered from 1 with no gaps
  (the first decision is `note:1`). A decision's title becomes the first line of the note's text
  (the title, a blank line, then the text; just the text when the two were the same), its reason
  becomes the note's why, and who decided it and when are kept. A fact's source becomes the
  note's why, and nobody decided it. Who recorded a decision is taken from the history of the
  decision (`unknown` when the file has none), and a fact keeps who recorded it. Replaced and
  withdrawn, confirmed and project-wide, and what each concerned (a fact that concerned a decision
  now concerns that decision's note) all carry over, and so does the history, on the new ids. A
  file written before names were verified (version 1) has no person or agent mark on its notes and
  history, and shows them as unknown. What a text says is left as it was written, so a
  `[[decision:7]]` or `[[fact:5]]` in it is plain text now: it names nothing, and `[[note:7]]` is
  how a note is named.

```
uv run python scripts/import_graph.py http://127.0.0.1:8000/mcp graph.json
```

## Markdown copy

```
uv run python scripts/export_markdown.py http://127.0.0.1:8000/mcp notes
```

`scripts/export_markdown.py` writes one file for every epic, story, task, acceptance criterion and
note of the project, named by its kind, its number and a short form of its title or, for a note, its first
line (`story-12-login.md` in the folder `stories`, `note-7-use-email.md` in `notes`), with its
fields (a note's status, who confirmed it and when, and who decided it, if anyone did) at the
top (an item's size too, when it has one) and everything it is connected to as `[[references]]`, to read in
Obsidian or keep in git. A story or task also lists the checks of the definition of done as a task
list, ticked or not, with who ticked each and how it was checked. The project's definition of done is one more file at the top of the
target folder, `definition-of-done.md`. Epics, stories and tasks are numbered each on its own, so `epic-1-…`,
`story-1-…` and `task-1-…` are three different files. It is read-only and is made again from the
graph each time, and the same graph gives the same files. It replaces the folders and the file it
made inside the target folder and refuses a target folder that holds something else, so nothing of
yours is overwritten.

A link made with `discovered_from` (the new work was found while doing the other) is named in the
copy as "discovered from" at the new item and "led to" at the one it was found in. Gates, drafted
plans and plan templates are not in the copy: it is for reading the work, and they are about how
the work is run. They are in the export.

## Bringing work in from other tools

Three scripts bring work that lives somewhere else into a project, as epics, stories and tasks with
their links and acceptance criteria. They are one way in: nothing is kept in step afterwards, and
Vrum never talks to Beads or GitHub (a script reads a file, or you run `gh` on your own machine and
give the script what it wrote). Each takes the server's address and a file, and each can say what
it would do first.

```
export VRUM_AGENT_KEY=<the key of the project>
bd export > beads-export.jsonl
uv run python scripts/import_beads.py http://127.0.0.1:8000/mcp beads-export.jsonl --dry-run
uv run python scripts/import_beads.py http://127.0.0.1:8000/mcp beads-export.jsonl
```

- **Beads** (`scripts/import_beads.py`) reads what `bd export` writes, one issue on each line. A
  `blocks`, `waits-for` or `conditional-blocks` dependency becomes `needs_first`, `related`
  becomes `relates_to`, `discovered-from` becomes `discovered_from`, and `duplicates` and
  `supersedes` keep their names. Any other kind of dependency is a problem, so that nothing is
  dropped without a word. Beads also keeps messages, events, molecules and gates, which are not
  work: the file is refused while it holds them, naming each one, and **`--skip-not-work`** leaves
  them out.
- **GitHub issues** (`scripts/import_github.py`) reads a JSON list of issues. Write it with the
  GitHub command line on your own machine, for example
  `gh issue list --repo OWNER/REPO --state all --limit 1000 --json number,title,body,state,stateReason,labels,milestone,url > issues.json`.
  A milestone is an epic and an issue is a story under it. An issue whose body has a task list of
  `- [ ] #12` makes those issues its tasks. A closed issue is done, or dropped when it was closed
  as not planned; a label that says urgent, high or low sets the priority. Pull requests in the
  list are left out and counted.
- **Spec Kit** (`scripts/import_speckit.py`) reads a `tasks.md`. The feature is the epic, each phase
  is a story, and each task line (`- [ ] T012 [P] [US1] Create the album model`) is a task, done
  when it is marked `[x]`. A `[US1]` label puts a task under the phase for that user story, and
  `(depends on T010, T011)` makes it wait for those tasks. A task inside a comment is not a task.

**The hierarchy is by position.** Vrum has three levels: an epic is an epic, what is directly under
an epic is a story, and what is under a story is a task. Work that has no epic goes under an epic
called **Inbox**, as stories: the convention for work that is not sorted yet, which Vrum's
suggestions for where work belongs start from (see [Sorting unsorted work](agents.md#sorting-unsorted-work)).
Nesting deeper than three levels, an epic inside something else, or a parent that is not in the file
is a listed problem, never guessed at. Statuses and priorities carry over, an acceptance criterion
in the source is placed on its story, and the old identifier (and, for GitHub, the issue's address
and labels) is kept in the item's details so the old system can still be found from the new one.
Nothing is invented: a size, a date or a link the source does not state is left out.

**A file that does not fit changes nothing.** Every problem is found first and listed, one sentence
each, and then nothing is made: "Nothing was created: the file has 3 problems." A plan the server
itself refuses, such as two items that wait for each other, is refused whole too. With `--dry-run`
a file that fits says what it would create ("It would create 2 epics, 2 stories and 2 tasks")
and creates nothing.

**The key needs two tools.** The scripts send the plan with `create_plan` and then set the status
each item had with `update_nodes`. The standard agent set has the first but not the second, so on
the project's **Users** page allow the key both. Without `update_nodes` the work is still made, but
every item starts as not started and the script says so. A project with a definition of done that
has checks refuses `done` from an agent, so a closed issue is made as not started and the script
says so too: tick the checks, or set it on the board.

## Setting up a coding agent

`scripts/setup_agent.py` writes what a coding agent needs to use a project, in the project's own
folder: its MCP configuration and Vrum's short agent guide.

```
uv run python scripts/setup_agent.py claude http://127.0.0.1:8000/mcp --dir ~/work/my-project --check
```

The clients are `claude` (Claude Code), `codex` (Codex), `cursor` (Cursor) and `copilot` (GitHub
Copilot, in the CLI and in VS Code). **It never writes the agent key.** Each configuration says to
read the key from the `VRUM_AGENT_KEY` environment variable, so the files are safe to commit and
the key stays where only you can read it: make the key on the project's **Users** page, then run
`export VRUM_AGENT_KEY=<the key>` before you start the client. The guide is the server's own
agent guide, written between two markers (`<!-- vrum-guide:start -->` and
`<!-- vrum-guide:end -->`) in the file the client reads (CLAUDE.md, AGENTS.md or
`.github/copilot-instructions.md`), so running it again replaces that block and leaves the rest of
the file alone, and it never makes a second copy. An existing configuration file is kept: the
`vrum` entry is added to it. A file that is not valid JSON, or a Codex configuration that already
has a Vrum table, is left alone and refused in words. With **`--check`** and the key set it also
connects and says which tools the key is offered.
