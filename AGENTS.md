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
- `src/vrum/db.py`: the PostgreSQL pool and the questions asked of the database.
- `src/vrum/server.py`: the MCP tools and the health route.
- `src/vrum/__main__.py`: starts the server. `src/vrum/healthcheck.py`: the container health probe.
- `tests/`: unit tests, plus `test_mcp_integration.py` (marked `integration`, driven by `./check.sh`).

## Working rules

- One milestone per iteration: the first unchecked one in `PROGRESS.md`. Never start the next one.
- A milestone is finished only when its "Proof" in `SPEC.md` passes against the real containers and a real MCP client, with every earlier proof still passing.
- Product decisions that `SPEC.md` does not answer go in `BLOCKED.md` (question, 2 to 3 options, a recommendation, the default you would take). Do not invent answers.
- Defaults the owner may veto are recorded in `DECISIONS.md` and listed under "Defaults taken" in `PROGRESS.md`.
- Never commit a secret. Configuration comes from the environment.
- Never touch any Deelio repository or the Taiga project.
- No placeholder or shell code, and no abstraction without a consumer.
- Keep every change small and reviewable. Plain English in every message a person or agent reads.
- The MCP SDK is v2 (`from mcp.server import MCPServer`, `from mcp import Client`); its documentation is at https://py.sdk.modelcontextprotocol.io/. Check it before using an SDK feature.
