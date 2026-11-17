# Backup, restore and export

Vrum keeps everything in one PostgreSQL database, in a Docker volume. There are two kinds of copy:
a **backup** of the whole database, for the person who runs the server, and an **export** of one
project, as data you can read, keep or load into another project.

`docker compose down` stops the stack and keeps the data. **`docker compose down --volumes` stops
it and deletes the data, accounts included.** Make a backup or an export first if you need it.

## Backup and restore of the whole database

- **Backup:** `./scripts/backup.sh` writes the whole database to one file, by default in `backups/`
  (which is not committed), while the server keeps running. It holds everything of every account and
  project, password hashes and keys' hashes included, so it is not an ordinary account's to take: it
  is for whoever runs the server. The file is readable only by you and is **not encrypted**, so keep
  it where only you can read it. Give a file name to choose where it goes: `./scripts/backup.sh
  my-backup.dump`.
- **Restore:** `./scripts/restore.sh <file>` puts it back, accounts and projects with it. Into a
  fresh database (after `docker compose down --volumes`) it just restores.
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
one on its **Agent access** page first (see [connecting an agent](agents.md)).

```
export VRUM_AGENT_KEY=<the key of the project>
uv run python scripts/export_graph.py http://127.0.0.1:8000/mcp graph.json
```

- **Export:** `scripts/export_graph.py` writes the whole graph of that project as one JSON file that
  you can read and keep: every item with its number or id, dates, confirmations and history. An
  epic, story or task is in it by its kind and number (`story:12`), and everything that points at
  one (a parent, a link, a requirement's story, what a decision or fact concerns, an event) names
  it that way. The file is readable only by you and holds everything in the project, so keep it
  private. Accounts, sessions and keys are never in it. The board's **Status and export** page has
  the same download.
- **Import:** `scripts/import_graph.py` loads such a file into an **empty project** (make one on the
  board, and a key for it), keeping every number, id, date, confirmation and the history, so
  `story:12` is still `story:12`, and the next new epic, story or task continues after the highest
  number of its own kind. It is all or nothing, and the file can be as big as one request may be
  (4 MiB unless you raise `VRUM_MAX_REQUEST_BYTES`). A project that already holds anything
  refuses. To move a bigger graph, back it up and restore it.

```
uv run python scripts/import_graph.py http://127.0.0.1:8000/mcp graph.json
```

## Markdown copy

```
uv run python scripts/export_markdown.py http://127.0.0.1:8000/mcp notes
```

`scripts/export_markdown.py` writes one file for every epic, story, task, requirement, decision and
fact of the project, named by its kind, its number and a short form of its title (`story-12-login.md`
in the folder `stories`), with its fields (including whether a person confirmed it, and whether it
is still in force) at the top and everything it is connected to as `[[references]]`, to read in
Obsidian or keep in git. Epics, stories and tasks are numbered each on its own, so `epic-1-…`,
`story-1-…` and `task-1-…` are three different files. It is read-only and is made again from the
graph each time, and the same graph gives the same files. It replaces the folders it made inside
the target folder and refuses a target folder that holds something else, so nothing of yours is
overwritten.
