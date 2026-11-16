#!/usr/bin/env bash
# Create .env with a random database password. There is no token to make: people sign in with an
# account they register on the board, and agents use a key the owner of a project creates there.
# If .env already exists it is left alone.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -e .env ]; then
  echo ".env already exists; leaving it alone." >&2
  exit 1
fi

password="$(openssl rand -hex 24)"
sed -e "s/^VRUM_DB_PASSWORD=.*/VRUM_DB_PASSWORD=${password}/" .env.example > .env
chmod 600 .env
echo "Created .env with a random database password."
