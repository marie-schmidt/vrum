#!/usr/bin/env bash
# Start Vrum and say where it is. This is `docker compose up -d --build --wait` followed by the
# address of the board. Anything after the script's name goes on to docker compose, and settings
# work as they do there: VRUM_PORT=8090 ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

if ! docker compose up -d --build --wait "$@"; then
  echo >&2
  echo "Vrum did not start. To see why, run: docker compose logs" >&2
  exit 1
fi

# Ask Docker where the port ended up, so that this is right whatever VRUM_PORT is set to.
inside="$(docker compose exec -T app printenv VRUM_PORT)"
port="$(docker compose port app "$inside")"
port="${port##*:}"

echo
echo "Vrum is now live."
echo "  Open the board:  http://localhost:${port}/board"
echo "  Agents connect:  http://localhost:${port}/mcp  (with a key from Settings & tools > Users)"
echo "  Stop it:         docker compose down   (add --volumes to also delete the data)"
