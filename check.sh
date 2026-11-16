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

echo "== integration tests (real containers, real MCP client, real headless browser)"
# The board's tests drive Playwright's Chromium, a development tool that is never in the app image.
# It is downloaded once and kept in your user cache.
uv run playwright install chromium
port="${VRUM_CHECK_PORT:-18000}"
export COMPOSE_PROJECT_NAME=vrum-check
# The scripted embedding service the checks use (tests/scripted_embeddings.py) comes from this
# file.
export COMPOSE_FILE=compose.yaml:compose.check.yaml
export VRUM_DB_PASSWORD="$(openssl rand -hex 16)"
# There is no token to make: the tests register an account, make a project and an agent key for it
# through the board's own API (tests/world.py), as a person would.
export VRUM_PORT="$port"
export VRUM_BIND_ADDRESS=127.0.0.1
# The tests that load or restore a database start a second, separate copy of Vrum (the compose
# project vrum-restore, built from compose.yaml alone) on this port. The tests make it and remove
# it; the trap below removes it too if a test dies.
export VRUM_RESTORE_TEST_PORT=$((port + 1))
trap 'docker compose down --volumes --remove-orphans >/dev/null 2>&1; COMPOSE_PROJECT_NAME=vrum-restore COMPOSE_FILE=compose.yaml docker compose down --volumes --remove-orphans --rmi local >/dev/null 2>&1' EXIT
docker compose up --detach --build --wait --wait-timeout 180
VRUM_TEST_URL="http://127.0.0.1:${port}/mcp" uv run pytest -m integration

echo "== all checks passed"
