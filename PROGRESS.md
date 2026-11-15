# Progress

Status: IN PROGRESS (M0 done; next milestone is M1)

## Inputs (from the loop file)

- PROJECT_NAME: vrum (already substituted in SPEC.md)
- TARGET_DIR: /Users/marieschmidt/Documents/Work/Start-Up/Projects/Vrum
- LEDGER_PATH: not provided, so M8 is skipped. When M8 is reached, tick it as "skipped (no ledger path)" without writing code. The `{{LEDGER_PATH}}` and `{{DECISIONS_PATH}}` placeholders in SPEC.md are left as they were.
- DECISIONS_PATH: not provided

## Milestones

Tick in order. Each tick records the date and the commit. Each milestone keeps an attempts line; after 3 failed attempts, write BLOCKED.md.

- [x] M0. Bootstrap (attempts: 1) (2026-11-14, commit a44cad8)
- [ ] M1. Graph core (attempts: 0)
- [ ] M2. Links, readiness and claims (attempts: 0)
- [ ] M3. Rules and coverage (attempts: 0)
- [ ] M4. Decisions and principles (attempts: 0)
- [ ] M5. Semantic index (attempts: 0)
- [ ] M6. Hardening (attempts: 0)
- [ ] M7. Export, import, backup, restore (attempts: 0)
- [ ] M8. Real-data trial (attempts: 0) (skipped, no ledger path: see Inputs)
- [ ] M9. Docs and acceptance (attempts: 0)

## Defaults taken (for owner's veto)

Full wording is in `DECISIONS.md`.

- M0: MCP SDK 2.x (`>=2.3,<3`), `uv` + `ruff` + `mypy --strict` + `pytest`, image `pgvector/pgvector:pg17`, `psycopg` 3 with its async pool.
- M0: no authentication until M6; the server is reachable only from this machine (compose binds `127.0.0.1`). `GET /health` is unauthenticated and says only `ok` or `unavailable`.
- M0: the database password comes from `VRUM_DB_PASSWORD` with no default (`./scripts/init-env.sh` writes a random one to the git-ignored `.env`); the database port is not published.
- M0: the server listens on `VRUM_PORT` (default 8000) and compose publishes the same port number.
- M0: the only tool is `server_status` (server, PostgreSQL and pgvector versions), so the client has something real to list; M1 adds the graph tools.

## M0 proof, as run

- `./check.sh`: lint, strict types, 9 unit tests and 3 integration tests (real containers, real MCP client) pass.
- From a clean clone: `./scripts/init-env.sh`, then `docker compose up -d`; both services reached `healthy` and a separate MCP client listed `server_status` and called it (PostgreSQL 17.12, pgvector 0.8.7).
- Also seen working: compose refuses to start without a password, a wrong `Host` header gets 421, `/health` returns 503 and `server_status` gives a plain refusal while the database is stopped, and both recover when it restarts.

## Notes

- Iteration 0 created the repository, SPEC.md, PROGRESS.md and AGENTS.md. M0 added the code, compose stack and `./check.sh` (see AGENTS.md for how to run them).
