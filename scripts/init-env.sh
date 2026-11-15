#!/usr/bin/env bash
# Create .env with a random database password. Refuses to overwrite an existing .env.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -e .env ]; then
  echo ".env already exists; leaving it alone." >&2
  exit 1
fi

password="$(openssl rand -hex 24)"
sed "s/^VRUM_DB_PASSWORD=.*/VRUM_DB_PASSWORD=${password}/" .env.example > .env
chmod 600 .env
echo "Created .env with a random database password."
