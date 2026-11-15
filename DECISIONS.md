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
