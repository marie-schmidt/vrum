#!/usr/bin/env bash
# The acceptance check: starts Vrum from this folder in a throwaway stack of its own (a fresh
# database, a random password; it never touches your own stack or your .env) and walks
# one scenario through it with a real MCP client, showing that every proof of every milestone holds.
#
#   ./acceptance.sh                run the whole scenario (a few minutes)
#   ./acceptance.sh finding board  run only these chapters (see tests/acceptance.py)
#
# It needs Docker with compose, uv and openssl, and nothing else: run it in a fresh clone to know
# that what is committed works. ./check.sh is the check to run before a commit (lint, types and
# hundreds of cases); this is the shorter story a person can read and trust. It uses the scripted
# embedding service of compose.check.yaml, so no model is downloaded. (The board, graph, context,
# searching, utilities, pages and accounts chapters drive Playwright's Chromium, which uv downloads
# the first time, about 100 MB. The demo chapter runs ./demo.sh, a separate stack of its own on the
# stack's port plus 4, its mail page on plus 5, its stand-in OpenID provider on plus 9 and its
# stand-in GitHub on plus 10, and removes it again.)
set -euo pipefail
cd "$(dirname "$0")"

need() {
  command -v "$1" > /dev/null 2>&1 || { echo "acceptance.sh needs $1 ($2)." >&2; exit 1; }
}
need docker "https://docs.docker.com/get-docker/"
need uv "https://docs.astral.sh/uv/"
need openssl "it makes the random database password"
docker compose version > /dev/null 2>&1 || { echo "acceptance.sh needs Docker with the compose plugin." >&2; exit 1; }
docker info > /dev/null 2>&1 || { echo "Docker is not running. Start it and try again." >&2; exit 1; }

port="${VRUM_ACCEPTANCE_PORT:-18400}"
# The compose project names can be changed (VRUM_ACCEPTANCE_PROJECT, VRUM_RESTORE_PROJECT) so that
# several walks can run on one machine at once.
export COMPOSE_PROJECT_NAME="${VRUM_ACCEPTANCE_PROJECT:-vrum-acceptance}"
export VRUM_RESTORE_PROJECT="${VRUM_RESTORE_PROJECT:-vrum-restore}"
export COMPOSE_FILE=compose.yaml:compose.check.yaml
export VRUM_DB_PASSWORD="$(openssl rand -hex 16)"
export VRUM_PORT="$port"
export VRUM_BIND_ADDRESS=127.0.0.1
# The scripted mail server's page and message list is published on this port (this machine only).
export VRUM_MAIL_PORT="${VRUM_ACCEPTANCE_MAIL_PORT:-$((port + 2))}"
export VRUM_TEST_MAIL_URL="http://127.0.0.1:${VRUM_MAIL_PORT}"
# The scripted OpenID provider and GitHub, Dex and the second copy of the server that uses Dex are
# published on this machine only, on the next free ports after the demo's (this stack's port plus
# 3, 6, 7 and 8; the demo's own on plus 9 and 10). The real directory is not published.
export VRUM_OIDC_PORT="${VRUM_ACCEPTANCE_OIDC_PORT:-$((port + 3))}"
export VRUM_GITHUB_PORT="${VRUM_ACCEPTANCE_GITHUB_PORT:-$((port + 6))}"
export VRUM_DEX_PORT="${VRUM_ACCEPTANCE_DEX_PORT:-$((port + 7))}"
export VRUM_DEX_APP_PORT="${VRUM_ACCEPTANCE_DEX_APP_PORT:-$((port + 8))}"
export VRUM_TEST_OIDC_URL="http://127.0.0.1:${VRUM_OIDC_PORT}"
export VRUM_TEST_GITHUB_URL="http://127.0.0.1:${VRUM_GITHUB_PORT}"
export VRUM_TEST_DEX_URL="http://dex.localhost:${VRUM_DEX_PORT}"
export VRUM_TEST_DEX_APP_URL="http://localhost:${VRUM_DEX_APP_PORT}"
# The graph chapter makes a second, empty copy of Vrum (compose project vrum-restore) for the one
# proof that needs a graph of a known size. It removes it itself; the trap below does too.
export VRUM_RESTORE_TEST_PORT=$((port + 1))
# The demo chapter runs ./demo.sh, which is a separate stack of its own (its own compose project,
# which has to say demo, and its own port): nothing of the walk's stack is used by it or touched.
export VRUM_ACCEPTANCE_DEMO_PROJECT="${VRUM_ACCEPTANCE_DEMO_PROJECT:-${COMPOSE_PROJECT_NAME}-demo}"
export VRUM_ACCEPTANCE_DEMO_PORT="${VRUM_ACCEPTANCE_DEMO_PORT:-$((port + 4))}"
export VRUM_DEMO_MAIL_PORT="${VRUM_ACCEPTANCE_DEMO_MAIL_PORT:-$((port + 5))}"
export VRUM_DEMO_OIDC_PORT="${VRUM_ACCEPTANCE_DEMO_OIDC_PORT:-$((port + 9))}"
export VRUM_DEMO_GITHUB_PORT="${VRUM_ACCEPTANCE_DEMO_GITHUB_PORT:-$((port + 10))}"

# Whatever happens, leave nothing behind: no containers, no volumes, no images of this stack, and
# of the demo's copy only what ./demo.sh stop removes (nothing, when the demo chapter did not run).
trap 'docker compose down --volumes --remove-orphans --rmi local > /dev/null 2>&1; COMPOSE_PROJECT_NAME="$VRUM_RESTORE_PROJECT" COMPOSE_FILE=compose.yaml docker compose down --volumes --remove-orphans --rmi local > /dev/null 2>&1; VRUM_DEMO_PROJECT="$VRUM_ACCEPTANCE_DEMO_PROJECT" VRUM_DEMO_PORT="$VRUM_ACCEPTANCE_DEMO_PORT" ./demo.sh stop > /dev/null 2>&1' EXIT
docker compose down --volumes --remove-orphans > /dev/null 2>&1 || true

echo "== the browser the board chapters use (Playwright's Chromium, a development tool only)"
uv run playwright install chromium

echo "== docker compose up: a fresh database and server, built from this folder"
docker compose up --detach --build --wait --wait-timeout 240
echo "   healthy: the database, the server, the scripted embedding service and mail server, the scripted OpenID provider and GitHub, Dex, the real directory and the second server"

echo "== the scenario"
VRUM_TEST_URL="http://127.0.0.1:${port}/mcp" \
  uv run python -m tests.acceptance "$@"
