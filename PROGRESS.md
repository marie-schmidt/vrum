# Progress

Status: IN PROGRESS (M0, M1 and M2 done; next milestone is M3)

## Inputs (from the loop file)

- PROJECT_NAME: vrum (already substituted in SPEC.md)
- TARGET_DIR: /Users/marieschmidt/Documents/Work/Start-Up/Projects/Vrum
- LEDGER_PATH: not provided, so M8 is skipped. When M8 is reached, tick it as "skipped (no ledger path)" without writing code. The `{{LEDGER_PATH}}` and `{{DECISIONS_PATH}}` placeholders in SPEC.md are left as they were.
- DECISIONS_PATH: not provided

## Milestones

Tick in order. Each tick records the date and the commit. Each milestone keeps an attempts line; after 3 failed attempts, write BLOCKED.md.

- [x] M0. Bootstrap (attempts: 1) (2026-11-14, commit a44cad8)
- [x] M1. Graph core (attempts: 1) (2026-11-15, commit ad4f5f7)
- [x] M2. Links, readiness and claims (attempts: 1) (2026-11-15, commit 33e0fcf)
- [ ] M3. Rules and coverage (attempts: 0)
- [ ] M4. Decisions and principles (attempts: 0)
- [ ] M5. Semantic index (attempts: 0)
- [ ] M6. Hardening (attempts: 0)
- [ ] M7. Export, import, backup, restore (attempts: 0)
- [ ] M8. Real-data trial (attempts: 0) (skipped, no ledger path: see Inputs)
- [ ] M9. Docs and acceptance (attempts: 0)
- [ ] M10. Board for humans (attempts: 0)

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

## M0 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 3 integration tests (real containers, real MCP client) pass.
- From a clean clone: `./scripts/init-env.sh`, then `docker compose up -d`; both services reached `healthy` and a separate MCP client listed `server_status` and called it (PostgreSQL 17.12, pgvector 0.8.7).
- Also seen working: compose refuses to start without a password, a wrong `Host` header gets 421, `/health` returns 503 and `server_status` gives a plain refusal while the database is stopped, and both recover when it restarts.

## M1 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 19 integration tests pass (real containers, real MCP client). The integration tests include the M0 proof, so it still passes.
- From a clean clone with plain `docker compose up -d`: the 19 integration tests pass against that stack, and a separate script showed each refusal as an agent reads it: a story without an epic, a task under an epic, a second parent, and a stale version. A valid epic, story and task were created and the story's history was read (create by marie, update by ann, with before and after and versions).
- The database refuses bad data on its own: tests run SQL directly in the database container for a story with no parent, a task under an epic, an epic with a parent, a story under a story, an unknown kind, a blank title, the stored status `ready`, a kind change, a change without a new version, rewriting or deleting an event, and deleting a node that has history.
- Eight parallel edits from the same version gave exactly one winner. Restarting the stack kept the data and did not re-apply the migration.

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

## Notes

- Iteration 0 created the repository, SPEC.md, PROGRESS.md and AGENTS.md. M0 added the code, compose stack and `./check.sh` (see AGENTS.md for how to run them). M1 added the graph core (`src/vrum/graph.py`, the first migration and six MCP tools). M2 added links, readiness and claims (`links.py`, `claims.py`, `common.py`, the second migration and seven more tools).
- M10 was added to `SPEC.md` by the owner after M0. It is the last milestone, after M9.
