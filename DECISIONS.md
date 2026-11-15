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

## 2026-11-15: choices made while building M4

These fill gaps the spec leaves open. None changes a spec default. The owner may veto any of them.

- **A decision is a record, never edited.** There is no `update_decision`. A change of mind is `supersede_decision`: it records a new decision and marks the old one `superseded`, pointing at its replacement, in one step. The database refuses to edit or delete a decision, and refuses to change a superseded one again. A typo is fixed the same way, by superseding. The old text stays, which is the point of a memory.
- **Who is who:** `decided_by` is the person who made the decision (required, the spec says a named person). `actor`, as everywhere, is whoever recorded it, often an agent. There is no list of known people, so `decided_by` is checked only for not being blank.
- **Required fields:** title, text, reason and `decided_by` cannot be blank. The spec lists a reason as a field of a decision, and a decision without its reason is poor memory. `decided_on` defaults to today (UTC) and can be given.
- **Replacing:** a decision can be superseded once. Two agents replacing it at the same moment give one winner; the other is told to supersede the replacement instead. A replacement may be of the other kind, so a decision can become a principle (and the reverse). Replacements are always recorded after what they replace, which the database checks, so they cannot form a circle.
- **Links carry over:** by default (`keep_links` true) a replacement affects what the old decision affected, so that it does not silently drop out of recall; ids given are added, and `keep_links` false starts it with only those. The old decision keeps its links as a record, and its links cannot be changed (the database refuses it); change the replacement's instead.
- **Links:** `link_decision` and `unlink_decision` take lists of node ids and rule ids and change nothing if any is wrong, missing, already linked or not linked. Only active decisions can be relinked.
- **Recall is by exact link and nothing else:** `recall_decisions` takes a node id or a rule id and returns that item's active decisions, every active principle (linked or not), and its linked decisions that have been superseded. Decisions linked to its parent or children are not inferred, so an agent recalls those as well if they matter. A linked principle appears under principles, not under decisions. Each of the three lists is paged on its own with `show` and `after_id`, with counts for all.
- **Active principles:** there is no separate tool; `list_decisions` with `kind=principle` and `status=active` is the list.
- **History:** every recording, link, unlink and supersession is an event. A link event also shows in the history of the node or rule it names. A decision's own history is `decision_history`. The old decision's `decision_superseded` event is in its own history only.

## 2026-11-15: the goal, the product shape and the research behind it (owner's request, loop's choices open to veto)

- **What the owner asked for:** a graph view and a Kanban view in M10, an overview of the MCP tools, and the other pages needed to see every part of the product, as a mix of GitHub Projects, Obsidian, Hindsight or Elasticsearch, and Taiga's MCP capabilities, "but better and complete", with the target goal "effective, efficient, agent and human friendly coding project management". The owner asked for each product to be researched so the intended overlap is clear.
- **Research:** six read-only research threads, each from the product's own documentation: the five products, plus what already works for agents and humans managing work together (Beads, Claude Code task lists, Linear, Task Master, Backlog.md, the Copilot cloud agent). Their conclusions are summarised in `SPEC.md` section 10, with a source for each product. Hindsight and the agent tools gave the clearest lessons: an agent needs one call for the next work with its context, handoff notes, and small answers; and memory must not be written by a language model.
- **Decided by:** the owner asked for the views and the goal. Everything below is the loop's choice, open to veto.

**What changed in the spec**

- **Goal and section 10** added to `SPEC.md`: the goal, a table of what Vrum takes from and deliberately leaves out of each product, the ten capabilities that matter most, and the list of pages.
- **M5 now also does exact-words search.** Reason: Elasticsearch and Hindsight both show that meaning-only search is weaker than words plus meaning, each labelled; PostgreSQL full-text search is in the one database, is never stale, and keeps working when the embedding service is down. It was going to be in M12; it is cheaper and safer to build it with the index.
- **New M5B, agent workflow**, placed right after M5 and named M5B so that no milestone is renumbered: `next_work` (claim the next work and get its context in one call), `finish_work` and a required handoff note, `create_plan` (a whole plan, all or nothing), `update_nodes` (up to 50 changes, reported one by one), `changes_since` (what changed after an event), compact lists, a similar-items check on create, and server instructions. This changes M2 (release now needs a note) and the shape of earlier answers (lists leave out bodies by default). Under the prototype stance that is done everywhere and the database is rebuilt.
- **M6** refuses text containing a well-known secret format (known token prefixes, private key blocks). It refuses instead of quietly rewriting the text, so nothing is changed behind the writer's back; only well-known formats are checked, to avoid false refusals.
- **M7** adds a read-only Markdown export (one file per item, links as `[[references]]`) next to the JSON export.
- **M9** adds an agent guide to the README, the same text as the server instructions.
- **M10** gains the graph view (global with a cap of 300 items, local at 1 to 3 steps, typed lines, overlap suggestions shown dashed), the page bar and shareable addresses, and progress counts on epics and stories.
- **New M11 to M13** (the other pages): Table, Outline, Rules and Memory with editing, and one filter language shared by people and agents; Search, Overlaps, Markdown, `[[references]]`, "Mentioned in" and a quick switcher; Home, Activity with "Who is working on what", Tools (generated from the live tool list, with a test that every tool is accounted for) and Status.

**Choices and defaults taken, open to veto**

- **Order inside the next-work call:** highest priority first, then oldest. `ready_work` keeps its oldest-first order. `next_work` also marks the item in progress, so the board shows it.
- **Handoff on release:** `release_claim` needs a note. A claim that lapses has none, and says so.
- **Plan size:** at most 200 items in one `create_plan`, at most 50 in `update_nodes`.
- **Graph:** drawn by the board's own code, no library, no internet. Pinned dot positions are kept in the browser only.
- **Views are addresses:** filters and layout live after the `#` in the address. The server stores no views.
- **Markdown (replaces M10's plain-text default):** M10 still shows plain text. M12 adds a small renderer: no raw HTML, no images, web links open in a new tab.
- **References:** `[[node:12]]`, `[[rule:3]]`, `[[decision:7]]`, exact ids only, no matching by title. They are text only and change no link, readiness or order. "Mentioned in" is worked out when asked and stored nowhere.
- **People can do what agents can**, except `reindex` and import. A person can claim, renew and release with a typed name. There is no override of someone else's claim: a claim lapses by itself.
- **The Tools page** has "try it" for tools that only read. Anything that changes data goes through the board's own forms.
- **Home becomes the landing page in M13.** Until then the board opens on the Kanban page.

**Questions for the owner, not built, each with a recommendation**

- **Taiga export.** The spec says Vrum must be able to export to Taiga later. Taiga imports a project dump that always creates a new project, and has no place for links, claims, rules or decisions. Recommendation: a one-off Taiga dump exporter after M10 that flattens to one epic per story and writes rules and decisions into descriptions; not before there is a reason.
- **Approval gate for plans.** Task Master and Backlog.md have a point where a person approves a plan before work starts. Recommendation: not now; `create_plan` creates ordinary `new` items that a person sees on the board and can drop. A real draft state can be added if the owner wants it.
- **Acceptance criteria checked before "done".** Recommendation: not now. Rules already are the requirements, and `finish_work` requires a handoff note saying what was verified.
- **Issues as a kind, a wiki entity, attachments, custom fields.** Taiga has them. Recommendation: none. Bodies, `[[references]]` and "Mentioned in" cover most of what a wiki is for; an issue is a task with a title that says so.
- **M7 backup and restore** (from the earlier decision): still recommended to drop the scripts and keep export and import.
