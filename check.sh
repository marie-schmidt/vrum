#!/usr/bin/env bash
# The one check command: lint, types, unit tests, then integration tests against freshly built
# containers in their own throwaway compose project (it never touches your running stack or .env).
set -euo pipefail
cd "$(dirname "$0")"

echo "== lint"
uv run ruff check .
uv run ruff format --check .

echo "== types"
uv run mypy

echo "== unit tests"
uv run pytest -m "not integration"

echo "== integration tests (real containers, real MCP client)"
port="${VRUM_CHECK_PORT:-18000}"
export COMPOSE_PROJECT_NAME=vrum-check
export VRUM_DB_PASSWORD="$(openssl rand -hex 16)"
export VRUM_PORT="$port"
export VRUM_BIND_ADDRESS=127.0.0.1
trap 'docker compose down --volumes --remove-orphans >/dev/null 2>&1' EXIT
docker compose up --detach --build --wait --wait-timeout 180
VRUM_TEST_URL="http://127.0.0.1:${port}/mcp" uv run pytest -m integration

echo "== all checks passed"
