# Decisions

The owner's decisions about vrum, newest last. The defaults in section 3 of `SPEC.md` stay in force unless an entry here changes them. Each change names who decided it.

## 2026-11-14: spec defaults adopted as written

- **What:** Python 3.12, the official MCP Python SDK, PostgreSQL with pgvector in the same compose file, streamable HTTP bound to localhost, locally hosted embeddings, and a caller-named actor on every write.
- **Decided by:** the loop, taking the defaults in `SPEC.md`. Waiting for the owner's veto.

## 2026-11-14: choices made while building M0

These fill gaps the spec leaves open. None changes a spec default. The owner may veto any of them.

- **MCP SDK version:** `mcp` 2.x (the SDK's current major version, with `MCPServer` and `Client`), pinned to `>=2.3,<3` and locked in `uv.lock`.
- **Python tooling:** `uv` for dependencies, `ruff` for lint and format, `mypy --strict` for types, `pytest` for tests.
- **Database image:** `pgvector/pgvector:pg17`. The database port is not published to the host; only the app is reachable.
- **Database driver:** `psycopg` 3 with its async connection pool.
- **No authentication yet:** the bearer token belongs to M6. Until then the server is reachable only from this machine because compose binds the port to `127.0.0.1`. Do not set `VRUM_BIND_ADDRESS` to anything else before M6 is done.
- **Health check:** `GET /health` is not authenticated, as the SDK requires of custom routes, and says only `ok` or `unavailable`.
- **Database password:** read from `VRUM_DB_PASSWORD`, with no default. `./scripts/init-env.sh` writes a random one to the git-ignored `.env`.
- **Port:** the server listens on `VRUM_PORT` (default 8000) and compose publishes the same number on the host, because the server only accepts requests addressed to `localhost:<that port>`.

## 2026-11-15: board for humans added (M10)

- **What:** `SPEC.md` now ends with M10, a Kanban board at `/board` on the same server, where people see agent work and add or refine epics, stories and tasks. This changes the earlier "no web UI" and "no boards" statements: the board is allowed, other web UI and sprints are still out. Edits made on the board go through the same operations as the MCP tools, and the semantic index is refreshed from them by the reindex job.
- **Defaults taken for M10, open to veto:** plain HTML, CSS and JavaScript with no build step and nothing loaded from the internet; sign-in with the existing bearer token; plain-text display with no Markdown rendering; Playwright for browser tests as a development dependency; the board checks for new events every 5 seconds; rules and decisions are read-only on the board.
- **Decided by:** the owner (@marie-schmidt), who asked for the board. The defaults above are the loop's.

## 2026-11-15: choices made while building M1

These fill gaps the spec leaves open. None changes a spec default. The owner may veto any of them.

- **Ids:** whole numbers given by the database, starting at 1, shared by epics, stories and tasks. They are short enough for an agent to type and read.
- **Priority:** four named levels, `low`, `normal` (the default), `high` and `urgent`, rather than a number whose direction an agent would have to guess.
- **Status:** a node can be `new`, `in_progress`, `blocked`, `done` or `dropped`, and can move freely between them; no workflow rules are invented. `ready` is never stored and cannot be set. M2 works it out from the links, so until then a node shows only its stored status.
- **One parent, one way to change it:** `move_node` is the only way to change a parent. `update_node` also accepts `parent_id` for one reason only: so that an attempt to give a node a second parent is refused with a plain message that points to `move_node`.
- **History:** every create, update and move is an event with the actor, the time, and the changed fields as they were and as they became, each with the node's version. A create lists every field. `node_history` returns them oldest first.
- **No deleting:** there is no delete tool (use status `dropped`). The database also refuses to delete a node that has history, which is every node, and refuses to change or delete an event.
- **Paging:** every list takes `limit` (1 to 200, default 50) and `after_id`, and returns `next_after_id` until the last page. Items come in id order.
- **Titles and actors:** a title or actor that is blank is refused; surrounding spaces are trimmed. Actors are free text, with no list of known actors.
- **Schema changes:** ~~numbered SQL files in `src/vrum/migrations/`~~ Replaced the same day by the owner's decision below: there are no migrations.

## 2026-11-15: choices made while building M2

These fill gaps the spec leaves open. None changes a spec default. The owner may veto any of them.

- **What `ready` shows:** the spec's rule is followed to the letter. A node nobody has started shows as `ready` when everything it needs first is done, and that includes a brand-new node that needs nothing. So the status `new` now means "not started, and waiting for something it needs first". What can be set is unchanged (`new`, `in_progress`, `blocked`, `done`, `dropped`); `ready` is only ever worked out, by the database view `node_state`, and `waiting_on` lists what a node still waits for.
- **Only `done` counts:** a prerequisite that is `dropped` or `blocked` keeps its dependents waiting. If a prerequisite no longer applies, remove the `needs_first` link.
- **Links:** allowed between any two nodes, whatever their kinds. `duplicates` and `relates_to` work both ways, so A to B is the same link as B to A. `supersedes`, `duplicates` and `relates_to` are records only: they change nothing else, such as a status. `list_links` was added so that what is written can be read.
- **Circles of waiting:** refused by the database, which takes a lock so that two links added at the same moment cannot each look fine and together make a circle. The refusal names the whole circle.
- **Claims:** only stories and tasks. A node can be claimed when it is `ready` or `in_progress` and nothing it needs first is open; done, dropped and blocked work and epics are refused with a reason. A claim does not change the node's status and does not stop other people editing the node: it says "I am working on this".
- **Leases:** `lease_seconds` is 1 to 86400 (24 hours), default 900 (15 minutes). A claim lapses on its own with no action. Only the holder can renew (and only before it lapses) or release. Another actor can take over a lapsed claim, and the history records who held it before.
- **`ready_work`:** stories and tasks that are ready and not claimed (`kind` can ask for epics instead), oldest first. Priority is shown on each item but does not change the order, so that paging stays simple.
- **History:** a link event shows in the history of both nodes. Claims and releases are events; a claim lapsing is not, because nothing happens at that moment.

## 2026-11-15: no migrations (owner's decision)

- **What:** the database has no migrations. The whole schema is one file, `src/vrum/schema.sql`, applied to an empty database when the server starts. To change the schema, edit that file and rebuild the database from scratch (`docker compose down --volumes`, then `docker compose up -d --build --wait`). This replaces the numbered migration files and their runner, which were built out of production habit.
- **Why:** Vrum is a prototype with no customer data. Migrations exist to keep a database that already holds real data alive through a change, and there is none to keep. They cost a runner, a history table and the discipline of never editing an applied file, and they would leave the schema as a pile of patches instead of one readable file.
- **One small safeguard kept:** the server records which version of the schema built the database, and refuses to start on a database built from a different one, saying in plain words to rebuild it. That replaces a confusing SQL error with one sentence. It is a few lines and can be removed if the owner prefers.
- **When to reconsider:** when a database holds data someone cares about.
- **Decided by:** the owner (@marie-schmidt).

## 2026-11-15: other production habits looked for (loop's findings, open to veto)

The owner asked the loop to look for other things done out of production habit. Nothing else in the code protects data or users that do not exist yet. What was found:

- **Removed with the migrations:** the proof step "restart the stack and check the data survived and nothing was applied twice". It only mattered because of the migration runner. A restart check that the data survives stays useful, but is no longer a proof step.
- **In `SPEC.md`, so the owner's call, not changed by the loop:** M7's backup and restore scripts and its "destroy the volume, restore" proof exist to protect data that does not exist yet. M7's full export and import as JSON is still useful (the Taiga export and the M8 trial need it). Recommendation: keep export and import, drop the backup and restore scripts and their proof until there is data worth saving. M6 hardening (token, size caps, logs without secrets) is spec'd as written and is cheap, so it stays.
- **Kept on purpose, cheap and asked for by the spec:** the database refusing bad data itself (`SPEC.md` section 1), versions on every edit, the append-only event log, and plain refusals.
- **Kept, small, and easy to drop if the owner wants:** the container runs as a non-root user, the image build is multi-stage, and strict type checking is on.
