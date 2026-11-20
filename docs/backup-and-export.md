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
  and export** page has the same download.
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
