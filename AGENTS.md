# AGENTS.md

How to work in this repository. Read `SPEC.md` first, then `PROGRESS.md`.

## Running the checks

`./check.sh` is the one check command. It runs, in order:

1. lint: `uv run ruff check .` and `uv run ruff format --check .`
2. types: `uv run mypy` (strict)
3. unit tests: `uv run pytest -m "not integration"`
4. integration tests: builds the image, starts the real database and server in a throwaway compose project (`vrum-check`, port 18000 or `VRUM_CHECK_PORT`, random password, removed afterwards), and runs `uv run pytest -m integration` with a real MCP client against it.

It needs Docker (with compose) and `uv`. It never touches your own stack or your `.env`. Run it before every commit; fix `ruff format` problems with `uv run ruff format .`.

## Running the stack

```
./scripts/init-env.sh      # once: writes .env with a random database password
docker compose up -d --build --wait
```

The MCP endpoint is `http://127.0.0.1:8000/mcp` (or `VRUM_PORT`). `GET /health` is the health check. `docker compose down` stops it; add `--volumes` to also delete the data.

## Layout

- `src/vrum/config.py`: settings from the environment (the only place that reads it).
- `src/vrum/db.py`: the PostgreSQL pool, transactions, and `prepare_schema`, which builds an empty database from `schema.sql` and refuses one built from any other version of it.
- `src/vrum/schema.sql`: the whole database in one file: tables, constraints, triggers and the `node_state`, `decision_state` and `memory_state` views.
- `src/vrum/common.py`: what the modules below share: `Refusal` (the plain-English refusals), argument checks (including `parse_day` for dates), paging, and database helpers.
- `src/vrum/graph.py`: the one owner of epics, stories and tasks (`Graph`), and of how a node is read (`node_state`, backed by the `node_state` view that decides what `ready` means).
- `src/vrum/links.py`: the one owner of links. `src/vrum/claims.py`: the one owner of claims and leases.
- `src/vrum/rules.py`: the one owner of rules and the coverage report. `src/vrum/decisions.py`: the one owner of decisions and principles, their links and supersession.
- `src/vrum/memories.py`: the one owner of facts and beliefs: their evidence and links, replacing and retracting them, confirming a belief and promoting it to a decision (staleness is worked out by the `memory_state` view). `src/vrum/recall.py`: `recall`, everything known about one node or rule by exact links, from decisions and memories.
- `src/vrum/server.py`: the MCP tools (thin: they call `Graph` and the other owners) and the health route.
- `src/vrum/__main__.py`: starts the server. `src/vrum/healthcheck.py`: the container health probe.
- `tests/`: unit tests, plus the integration tests (marked `integration`, driven by `./check.sh`): `test_mcp_integration.py`, `test_graph_integration.py`, `test_links_and_claims_integration.py`, `test_rules_integration.py`, `test_decisions_integration.py` and `test_memories_integration.py` go through a real MCP client; `test_database_constraints.py` runs SQL inside the real database container to prove the database refuses bad data on its own. `helpers.py` holds the small shared test helpers.

## Working rules

- One milestone per iteration: the first unchecked one in `PROGRESS.md`. Never start the next one.
- A milestone is finished only when its "Proof" in `SPEC.md` passes against the real containers and a real MCP client, with every earlier proof still passing.
- Product decisions that `SPEC.md` does not answer go in `BLOCKED.md` (question, 2 to 3 options, a recommendation, the default you would take). Do not invent answers.
- Defaults the owner may veto are recorded in `DECISIONS.md` and listed under "Defaults taken" in `PROGRESS.md`.
- **No migrations.** Vrum is a prototype with no customer data. The whole database is `src/vrum/schema.sql`. To change the schema, edit that file, tidying it as if it had always been this way, then rebuild the database from scratch: `docker compose down --volumes`, then `docker compose up -d --build --wait` (`./check.sh` already builds a fresh one every run). Do not write migration files, `ALTER TABLE` patches or data-preserving upgrade steps. Reconsider only when a database holds data someone cares about. (Owner's decision, see `DECISIONS.md`.)
- **Prototype stance.** Do not carry production habits that protect data or users that do not exist yet: no backward compatibility, deprecation paths or upgrade steps. When a design changes, change it everywhere and rebuild. Anything `SPEC.md` asks for is still built as written.
- Never commit a secret. Configuration comes from the environment.
- Never touch any Deelio repository or the Taiga project.
- No placeholder or shell code, and no abstraction without a consumer.
- Keep every change small and reviewable. Plain English in every message a person or agent reads.
- The MCP SDK is v2 (`from mcp.server import MCPServer`, `from mcp import Client`); its documentation is at https://py.sdk.modelcontextprotocol.io/. Check it before using an SDK feature.
