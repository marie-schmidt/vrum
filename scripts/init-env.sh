#!/usr/bin/env bash
# Create .env with a random database password and a random token. If .env already exists, add the
# token to it if it has none (a .env from before the token existed), and change nothing else.
set -euo pipefail
cd "$(dirname "$0")/.."

token="$(openssl rand -hex 24)"

if [ -e .env ]; then
  if grep -q '^VRUM_TOKEN=.' .env; then
    echo ".env already exists and has a token; leaving it alone." >&2
    exit 1
  fi
  # Drop an empty VRUM_TOKEN= line, if there is one, then add the token.
  grep -v '^VRUM_TOKEN=$' .env > .env.new || true
  printf '\n# Every request to the server has to carry this token (Authorization: Bearer <token>).\nVRUM_TOKEN=%s\n' "$token" >> .env.new
  chmod 600 .env.new
  mv .env.new .env
  echo "Added a random token to the existing .env."
  exit 0
fi

password="$(openssl rand -hex 24)"
sed -e "s/^VRUM_DB_PASSWORD=.*/VRUM_DB_PASSWORD=${password}/" \
    -e "s/^VRUM_TOKEN=.*/VRUM_TOKEN=${token}/" .env.example > .env
chmod 600 .env
echo "Created .env with a random database password and a random token."
