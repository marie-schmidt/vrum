#!/usr/bin/env bash
# The acceptance check: starts Vrum from this folder in a throwaway stack of its own (a fresh
# database, a random token and password; it never touches your own stack or your .env) and walks
# one scenario through it with a real MCP client, showing that every proof of every milestone holds.
#
#   ./acceptance.sh                run the whole scenario (a few minutes)
#   ./acceptance.sh finding model  run only these chapters (see tests/acceptance.py)
#
# It needs Docker with compose, uv and openssl, and nothing else: run it in a fresh clone to know
# that what is committed works. ./check.sh is the check to run before a commit (lint, types and
# hundreds of cases); this is the shorter story a person can read and trust. It uses the scripted
# embedding service and language model of compose.check.yaml, so no model is downloaded.
set -euo pipefail
cd "$(dirname "$0")"

need() {
  command -v "$1" > /dev/null 2>&1 || { echo "acceptance.sh needs $1 ($2)." >&2; exit 1; }
}
need docker "https://docs.docker.com/get-docker/"
need uv "https://docs.astral.sh/uv/"
need openssl "it makes the random token and password"
docker compose version > /dev/null 2>&1 || { echo "acceptance.sh needs Docker with the compose plugin." >&2; exit 1; }
docker info > /dev/null 2>&1 || { echo "Docker is not running. Start it and try again." >&2; exit 1; }

port="${VRUM_ACCEPTANCE_PORT:-18400}"
export COMPOSE_PROJECT_NAME=vrum-acceptance
export COMPOSE_FILE=compose.yaml:compose.check.yaml
export VRUM_DB_PASSWORD="$(openssl rand -hex 16)"
export VRUM_TOKEN="$(openssl rand -hex 24)"
export VRUM_PORT="$port"
export VRUM_NO_MODEL_PORT=$((port + 1))
export VRUM_MODEL_SCRIPT_PORT=$((port + 2))
export VRUM_BIND_ADDRESS=127.0.0.1

# Whatever happens, leave nothing behind: no containers, no volumes, no images of this stack.
trap 'docker compose down --volumes --remove-orphans --rmi local > /dev/null 2>&1' EXIT
docker compose down --volumes --remove-orphans > /dev/null 2>&1 || true

echo "== docker compose up: a fresh database and server, built from this folder"
docker compose up --detach --build --wait --wait-timeout 240
echo "   healthy: the database, the server, a second server with no language model, and the"
echo "   scripted embedding service and language model"

echo "== the scenario"
VRUM_TEST_URL="http://127.0.0.1:${port}/mcp" \
  VRUM_NO_MODEL_TEST_URL="http://127.0.0.1:${VRUM_NO_MODEL_PORT}/mcp" \
  VRUM_TEST_MODEL_URL="http://127.0.0.1:${VRUM_MODEL_SCRIPT_PORT}" \
  uv run python -m tests.acceptance "$@"
