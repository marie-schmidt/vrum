#!/usr/bin/env bash
# Back up the whole database (everything Vrum keeps, search index included) to one file.
#
#   ./scripts/backup.sh [file]        default: backups/vrum-<date and time>.dump
#
# The server can keep running: the backup is one consistent snapshot of the database. The file
# holds all your data, so it is written readable only by you; keep it where only you can read it.
# Put it back with ./scripts/restore.sh. (For a copy you can read, diff or load into another
# server as data, use scripts/export_graph.py and scripts/export_markdown.py instead.)
set -euo pipefail
cd "$(dirname "$0")/.."

umask 077
file="${1:-backups/vrum-$(date -u +%Y%m%dT%H%M%SZ).dump}"
mkdir -p "$(dirname "$file")"
partial="$file.partial"
trap 'rm -f "$partial"' EXIT

if ! docker compose exec -T db pg_dump -U vrum -d vrum --format=custom --no-owner > "$partial"; then
  echo "The backup failed and nothing was written. Is the database running? Start it with:" >&2
  echo "  docker compose up -d --wait" >&2
  exit 1
fi
# A backup that cannot be read back is not a backup: check it before keeping it.
if ! docker compose exec -T db pg_restore --list < "$partial" > /dev/null; then
  echo "The backup was written but cannot be read back, so it was thrown away. Try again." >&2
  exit 1
fi
mv "$partial" "$file"
echo "Backed up to $file ($(wc -c < "$file" | tr -d ' ') bytes)."
