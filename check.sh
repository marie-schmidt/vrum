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
# The scripted embedding service, the scripted mail server, the scripted OpenID provider and GitHub,
# Dex and the real directory the checks use (tests/scripted_*.py, tests/providers/) come from this
# file.
export COMPOSE_FILE=compose.yaml:compose.check.yaml
export VRUM_DB_PASSWORD="$(openssl rand -hex 16)"
# There is no token to make: the tests register an account, make a project and an agent key for it
# through the board's own API (tests/world.py), as a person would.
export VRUM_PORT="$port"
export VRUM_BIND_ADDRESS=127.0.0.1
# The scripted mail server's page and message list (tests/scripted_mail.py) is published on this
# port, on this machine only. The tests that send email read what it was sent there.
mail_port="${VRUM_CHECK_MAIL_PORT:-$((port + 2))}"
export VRUM_MAIL_PORT="$mail_port"
# The stand-ins and real providers for signing in with another provider, each published on this
# machine only: the scripted OpenID provider (its sign-in page, control endpoint and log), the
# scripted GitHub, Dex (a real OpenID provider) and the second copy of the server that uses Dex.
# The tests read them at the VRUM_TEST_* addresses below; the real directory (OpenLDAP) is not
# published, and the tests reach it with docker compose exec.
oidc_port="${VRUM_CHECK_OIDC_PORT:-$((port + 3))}"
github_port="${VRUM_CHECK_GITHUB_PORT:-$((port + 4))}"
dex_port="${VRUM_CHECK_DEX_PORT:-$((port + 5))}"
dex_app_port="${VRUM_CHECK_DEX_APP_PORT:-$((port + 6))}"
export VRUM_OIDC_PORT="$oidc_port" VRUM_GITHUB_PORT="$github_port"
export VRUM_DEX_PORT="$dex_port" VRUM_DEX_APP_PORT="$dex_app_port"
# The tests that load or restore a database start a second, separate copy of Vrum (the compose
# project vrum-restore, built from compose.yaml alone) on this port. The tests make it and remove
# it; the trap below removes it too if a test dies.
export VRUM_RESTORE_TEST_PORT=$((port + 1))
trap 'docker compose down --volumes --remove-orphans >/dev/null 2>&1; COMPOSE_PROJECT_NAME=vrum-restore COMPOSE_FILE=compose.yaml docker compose down --volumes --remove-orphans --rmi local >/dev/null 2>&1' EXIT
docker compose up --detach --build --wait --wait-timeout 240
VRUM_TEST_URL="http://127.0.0.1:${port}/mcp" VRUM_TEST_MAIL_URL="http://127.0.0.1:${mail_port}" \
  VRUM_TEST_OIDC_URL="http://127.0.0.1:${oidc_port}" \
  VRUM_TEST_GITHUB_URL="http://127.0.0.1:${github_port}" \
  VRUM_TEST_DEX_URL="http://dex.localhost:${dex_port}" \
  VRUM_TEST_DEX_APP_URL="http://localhost:${dex_app_port}" \
  uv run pytest -m integration

echo "== all checks passed"
