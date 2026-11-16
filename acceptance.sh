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
# searching, utilities and accounts chapters drive Playwright's Chromium, which uv downloads the
# first time, about 100 MB.)
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
export COMPOSE_PROJECT_NAME=vrum-acceptance
export COMPOSE_FILE=compose.yaml:compose.check.yaml
export VRUM_DB_PASSWORD="$(openssl rand -hex 16)"
export VRUM_PORT="$port"
export VRUM_BIND_ADDRESS=127.0.0.1
# The graph chapter makes a second, empty copy of Vrum (compose project vrum-restore) for the one
# proof that needs a graph of a known size. It removes it itself; the trap below does too.
export VRUM_RESTORE_TEST_PORT=$((port + 1))

# Whatever happens, leave nothing behind: no containers, no volumes, no images of this stack.
trap 'docker compose down --volumes --remove-orphans --rmi local > /dev/null 2>&1; COMPOSE_PROJECT_NAME=vrum-restore COMPOSE_FILE=compose.yaml docker compose down --volumes --remove-orphans --rmi local > /dev/null 2>&1' EXIT
docker compose down --volumes --remove-orphans > /dev/null 2>&1 || true

echo "== the browser the board chapters use (Playwright's Chromium, a development tool only)"
uv run playwright install chromium

echo "== docker compose up: a fresh database and server, built from this folder"
docker compose up --detach --build --wait --wait-timeout 240
echo "   healthy: the database, the server and the scripted embedding service"

echo "== the scenario"
VRUM_TEST_URL="http://127.0.0.1:${port}/mcp" \
  uv run python -m tests.acceptance "$@"
