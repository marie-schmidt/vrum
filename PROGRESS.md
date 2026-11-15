# Progress

Status: IN PROGRESS (M0 to M4 and M1A done; next is the retro-fix M4A (facts and beliefs), then M5)

## Inputs (from the loop file)

- PROJECT_NAME: vrum (already substituted in SPEC.md)
- TARGET_DIR: /Users/marieschmidt/Documents/Work/Start-Up/Projects/Vrum
- LEDGER_PATH: not provided, so M8 is skipped. When M8 is reached, tick it as "skipped (no ledger path)" without writing code. The `{{LEDGER_PATH}}` and `{{DECISIONS_PATH}}` placeholders in SPEC.md are left as they were.
- DECISIONS_PATH: not provided

## Milestones

Tick in order. Each tick records the date and the commit. Each milestone keeps an attempts line; after 3 failed attempts, write BLOCKED.md.

- [x] M0. Bootstrap (attempts: 1) (2026-11-14, commit a44cad8)
- [x] M1. Graph core (attempts: 1) (2026-11-15, commit ad4f5f7)
- [x] M1A. Dates (retro-fix of M1) (attempts: 1) (2026-11-15, commit d85ff0b)
- [x] M2. Links, readiness and claims (attempts: 1) (2026-11-15, commit 33e0fcf)
- [x] M3. Rules and coverage (attempts: 1) (2026-11-15, commit fc080db)
- [x] M4. Decisions and principles (attempts: 1) (2026-11-15, commit 720b08b)
- [ ] M4A. Facts and beliefs (retro-fix of M4, run before M5) (attempts: 0)
- [ ] M5. Search and the semantic index (attempts: 0)
- [ ] M5B. Agent workflow (attempts: 0)
- [ ] M5C. Extraction and reflection (needs a configured language model) (attempts: 0)
- [ ] M6. Hardening (attempts: 0)
- [ ] M7. Export, import, backup, restore (attempts: 0)
- [ ] M8. Real-data trial (attempts: 0) (skipped, no ledger path: see Inputs)
- [ ] M9. Docs and acceptance (attempts: 0)
- [ ] M10. Board for humans: Kanban and graph (attempts: 0)
- [ ] M11. More views of the work (attempts: 0)
- [ ] M12. Rules and memory on the board (attempts: 0)
- [ ] M13. Find and connect (attempts: 0)
- [ ] M14. Run and see it all (attempts: 0)

## Defaults taken (for owner's veto)

Full wording is in `DECISIONS.md`.

- M0: MCP SDK 2.x (`>=2.3,<3`), `uv` + `ruff` + `mypy --strict` + `pytest`, image `pgvector/pgvector:pg17`, `psycopg` 3 with its async pool.
- M0: no authentication until M6; the server is reachable only from this machine (compose binds `127.0.0.1`). `GET /health` is unauthenticated and says only `ok` or `unavailable`.
- M0: the database password comes from `VRUM_DB_PASSWORD` with no default (`./scripts/init-env.sh` writes a random one to the git-ignored `.env`); the database port is not published.
- M0: the server listens on `VRUM_PORT` (default 8000) and compose publishes the same port number.
- M0: the only tool is `server_status` (server, PostgreSQL and pgvector versions), so the client has something real to list; M1 adds the graph tools.

- M1: ids are whole numbers from the database; priority is `low`, `normal`, `high` or `urgent`; stored status is `new`, `in_progress`, `blocked`, `done` or `dropped` (`ready` is computed from M2 on and never stored); `update_node` accepts `parent_id` only to refuse a second parent and point to `move_node`.
- M1: history lists the changed fields before and after with versions; there is no delete (use `dropped`); every list takes `limit` (1 to 200, default 50) and `after_id`; schema changes were numbered SQL files, replaced the same day by the owner's decision: there are no migrations, see below.

- M2: a node nobody has started shows as `ready` when everything it needs first is done (so a new node that needs nothing is `ready`, and `new` means "waiting for something it needs first"); only `done` counts, so a dropped or blocked prerequisite keeps dependents waiting; `waiting_on` lists what a node waits for.
- M2: links work between any two nodes; `duplicates` and `relates_to` work both ways; `supersedes`, `duplicates` and `relates_to` are records only; `list_links` was added so links can be read.
- M2: claims are for stories and tasks that are ready or in progress with nothing open that they need first; a claim does not change status or stop others editing; leases are 1 second to 24 hours, default 15 minutes, lapse on their own, and only the holder renews or releases; `ready_work` lists free stories and tasks oldest first (priority is shown, not used for order).

- Owner's decision, 2026-11-15: no migrations. The whole database is `src/vrum/schema.sql`; a schema change means rebuilding the database from scratch. The server refuses to start on a database built from a different version of the schema, in plain words. The loop's other findings about production habits, including a recommendation about M7's backup and restore, are in `DECISIONS.md`.

- M3: rules have their own ids, a title and body, and a status of `held` (the default), `settled` or `dropped`, which can change freely. A rule is created unplaced or in a story. `place_rule` puts an unplaced rule in a story and refuses one that is already placed; `move_rule` moves a placed rule. Both, and every other change to a rule, need the version last read.
- M3: a rule event shows in the history of the rule and of each story it touches (the story it is in, and the one it left); a rule's own history is `rule_history`.
- M3: the coverage report leaves out dropped rules (they are not unplaced) and dropped stories (they need no rules), and a story whose only rules are dropped counts as having none. Each of its two lists is paged on its own with `show`.

- M4: a decision is a record and is never edited; a change of mind is `supersede_decision`, which records a new decision and marks the old one superseded in one step. `decided_by` (the person) is separate from `actor` (who recorded it); title, text, reason and `decided_by` are required.
- M4: a replacement carries over what the old decision affected unless `keep_links` is false; the old one keeps its links as a record and they cannot be changed. A decision can be superseded once, and a replacement may be of the other kind.
- M4: `recall_decisions` works by exact link only (a node or a rule): its active decisions, every active principle, and its linked decisions that were superseded; nothing is inferred from parents or children. There is no separate principles tool: `list_decisions` with `kind=principle` and `status=active`.

- M1A: dates are plain days written YYYY-MM-DD, read by the server and refused in plain words when badly written; a date is removed only by asking (`clear_start_on`, `clear_target_on`); the start may equal the target. Overdue, the worked-out span (own dates win; otherwise the span of what is under it, leaving out dropped work) and schedule conflicts (a warning, never a refusal; a done or undated prerequisite does not conflict) are computed in the `node_state` view. `list_nodes` gained inclusive date filters and `overdue`.

## M0 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 3 integration tests (real containers, real MCP client) pass.
- From a clean clone: `./scripts/init-env.sh`, then `docker compose up -d`; both services reached `healthy` and a separate MCP client listed `server_status` and called it (PostgreSQL 17.12, pgvector 0.8.7).
- Also seen working: compose refuses to start without a password, a wrong `Host` header gets 421, `/health` returns 503 and `server_status` gives a plain refusal while the database is stopped, and both recover when it restarts.

## M1 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 19 integration tests pass (real containers, real MCP client). The integration tests include the M0 proof, so it still passes.
- From a clean clone with plain `docker compose up -d`: the 19 integration tests pass against that stack, and a separate script showed each refusal as an agent reads it: a story without an epic, a task under an epic, a second parent, and a stale version. A valid epic, story and task were created and the story's history was read (create by marie, update by ann, with before and after and versions).
- The database refuses bad data on its own: tests run SQL directly in the database container for a story with no parent, a task under an epic, an epic with a parent, a story under a story, an unknown kind, a blank title, the stored status `ready`, a kind change, a change without a new version, rewriting or deleting an event, and deleting a node that has history.
- Eight parallel edits from the same version gave exactly one winner. Restarting the stack kept the data and did not re-apply the migration.

## M1A proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 85 integration tests pass on a database built from scratch. Every earlier proof is still covered by them.
- From a clean clone of the commit, started as the separate compose project `vrum-proof` on its own port: the 85 integration tests pass, and a separate script showed each proof statement:
  - A start date after the target date is refused, on create and on update, with a plain message; the database also refuses it on its own (SQL test).
  - Dates set and cleared are shown by `get_node`, and the history lists exactly the dates that changed, with before and after and the actor.
  - A target date that has passed makes a node overdue until it is marked done.
  - A story and an epic with no dates report the span of what is under them, marked as worked out; given dates of their own they report those.
  - A schedule conflict shows on a node that starts before something it needs is due, and goes away when either date changes (and when the prerequisite is done).
  - A stale version is still refused for a date change.
- The test suite also covers: clearing and its refusals, loosely written dates (seven kinds, each with the plain message), same-day start and target, dropped work left out of spans, a task that never works a span out, dates that survive a move and a claim, and the date filters.
- One fix found along the way: the new `overdue` column was empty for nodes with no target date, and the model refused it; it is now false there.

## M2 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 36 integration tests pass (real containers, real MCP client). Every earlier proof is still covered by them.
- From a clean clone with plain `docker compose up -d`: the 36 integration tests pass, and a separate script showed each proof statement:
  - A cycle (A needs B needs C needs A) is refused with a message that names the whole circle.
  - B waits for A and shows `new` with `waiting_on [A]`; once A is done B shows `ready` with nothing it waits for.
  - Ten parallel claimers on one ready task gave exactly one winner (agent-0), and the other nine were told who holds it.
  - After a 3 second lease lapsed, nobody held the task and another actor claimed it; the history shows both claims.
- The database refuses bad links and claims on its own (tests run SQL directly): a link to the same node, an unknown type, a missing node, a circle, a repeated link, a two-way link repeated the other way round, a claim on an epic, a claim naming the wrong kind, a claim with no holder or no time, and a second holder of one task.
- Race check: with the lock in the cycle trigger removed, the parallel-links test fails every time, so it does guard the behaviour. Eight edits, ten claims and five pairs of opposite links added at the same moment each had exactly one winner.
- Restarting the stack kept the data and did not re-apply either migration.

## M3 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 52 integration tests pass on a database built from scratch from `schema.sql`. Every earlier proof is still covered by them.
- From a clean clone, started as the separate compose project `vrum-proof` on its own port: the 52 integration tests pass, and a separate script showed each proof statement:
  - A rule placed in one story cannot be placed in a second: the refusal says it belongs to at most one story and points to `move_rule`, and the rule is still only in the first story.
  - A move into an epic is refused and leaves the rule where it was. Two movers at once gave one winner, and the history reads created, placed, moved with each step's before and after and version; the move shows in the histories of both stories.
  - The coverage report lists the unplaced rule and the stories with no rule, and leaves out the story that holds one.
- The database refuses bad rules on its own (tests run SQL directly): a rule in an epic, a task or a story that does not exist, a blank title, an unknown status, a move into a task, a change without a new version, and events that name no rule, a rule and a node action mixed, or nothing at all. No table pairs rules with stories.
- Race check: with the row lock removed from `move_rule` and `place_rule`, both parallel tests fail on every run (six rounds each), so they guard the behaviour.
- The schema guard: a database whose stored schema marker is wrong, or missing, makes a second server refuse to start, saying to rebuild with `docker compose down --volumes`.

## M4 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 66 integration tests pass on a database built from scratch. Every earlier proof is still covered by them.
- From a clean clone of the commit, started as the separate compose project `vrum-proof` on its own port: the 66 integration tests pass, and a separate script showed each proof statement:
  - Recalling a story returned its two linked active decisions and the active principles (the linked one and one linked to nothing), and left out a decision linked to another story and a decision linked to nothing.
  - A decision replaced by a newer one was shown under superseded, with the id of what replaced it, and was not listed as active anywhere. Its title, text and reason were exactly as recorded, and its history reads recorded, linked, superseded.
  - A story with no decisions got no decisions back, only the principles: nothing is invented.
  - Superseding the same decision again is refused in plain words and points to its replacement.
- The database keeps decisions as a record on its own (tests run SQL directly): it refuses a blank title, text, reason or decider, an unknown kind or status, a decision marked superseded without a replacement (or the reverse), a replacement recorded earlier than what it replaces, an edit to what was decided or who decided it, bringing a superseded decision back, pointing it at another replacement, deleting a decision, links to nodes or rules that do not exist, a repeated link, and any change to the links of a superseded decision.
- Race check: with the row lock removed from `supersede_decision`, the test of six agents superseding one decision at once fails on every run. It first passed without the lock, because the database's own trigger stops the second one; the test now also requires the loser to get the plain message, not a database error, and that is what catches it.
- Housekeeping found along the way: tests that read `ready_work` now read every page (they broke once the shared test database held more than 200 ready items); a test that guessed at table names was replaced by the precise rule (the only table with a `story_id` is `rules`).

## Notes

- A GitHub repository was created on the owner's request: https://github.com/marie-schmidt/vrum (private).
- macOS or a sync tool left stale copies named like `AGENTS 2.md`, `db 2.py` and a git ref `main 2`. They were deleted and `.gitignore` now ignores `* 2` and `* 2.*`. Existing files are now changed with edits instead of replacing them by renaming a new file over them.
- Spec change on the owner's request (2026-11-15), after research on GitHub Projects, Obsidian, Hindsight, Elasticsearch, Taiga and agent task tools: new goal statement and section 10 ("Product shape"); M5 now also does exact-words search; new M5B (agent workflow) after M5; M6 refuses pasted secrets; M7 adds a Markdown export; M9 adds an agent guide; M10 gains the graph view.
- Second spec change on the owner's request (2026-11-15): (1) the whole graph is now one of the views, not the main navigation; (2) facts and beliefs like Hindsight's are in, as the retro-fix M4A (explicit facts and beliefs with evidence, one recall) and M5C (a model, if configured, proposes facts, draws up beliefs and answers questions, all labelled and checked); (3) a roadmap like GitHub Projects' is in, which needs dates on nodes, as the retro-fix M1A. The UI milestones are now M10 (Kanban and graph), M11 (Table, Outline, Roadmap), M12 (Rules and memory), M13 (Find and connect) and M14 (Run and see it all). M1A and M4A change the already-built M1 and M4 (the database is rebuilt, with no upgrade step) and are listed where they belong so that they run first.
- Every choice is in `DECISIONS.md` and open to the owner's veto.
- Third spec change on the owner's question (2026-11-15): M4A and M5C now say what happens when a belief stops being true. `supersede_memory` replaces several at once, `retract_memory` can retract the beliefs resting on an item, a belief a person confirmed is changed only with the person's name and words, and M5C's `find_contradictions` proposes which beliefs a new statement clashes with. M12's Memory page and the proofs follow.
- Iteration 0 created the repository, SPEC.md, PROGRESS.md and AGENTS.md. M0 added the code, compose stack and `./check.sh` (see AGENTS.md for how to run them). M1 added the graph core (`src/vrum/graph.py`, the first migration and six MCP tools). M2 added links, readiness and claims (`links.py`, `claims.py`, `common.py` and seven more tools). M3 added rules and coverage (`rules.py` and eight more tools) and, on the owner's instruction, replaced the migrations with the single `schema.sql`.
- M10 was added to `SPEC.md` by the owner after M0. It is the last milestone, after M9.
