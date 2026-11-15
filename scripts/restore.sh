#!/usr/bin/env bash
# Put back a backup made by ./scripts/backup.sh.
#
#   ./scripts/restore.sh <backup file> [--replace]
#
# Into a fresh database (after `docker compose down --volumes`, or on a new machine) it just
# restores. If the database already holds data it refuses, unless you say --replace, which throws
# that data away. The backup is restored into a new database first and swapped in only when it has
# been restored completely, so a backup that cannot be restored never costs the data that is there.
# The server is stopped while this goes on and started again at the end. The server of this
# checkout has to be built from the same version of the schema as the one that made the backup,
# or it refuses to start and says so.
set -euo pipefail
cd "$(dirname "$0")/.."

usage() {
  echo "Usage: ./scripts/restore.sh <backup file> [--replace]" >&2
  exit 2
}
[ $# -ge 1 ] && [ $# -le 2 ] || usage
file="$1"
replace="${2:-}"
[ -z "$replace" ] || [ "$replace" = "--replace" ] || usage
[ -f "$file" ] || { echo "There is no backup file $file." >&2; exit 1; }

psql_db() { docker compose exec -T db psql -U vrum -v ON_ERROR_STOP=1 -tA "$@"; }

echo "Starting the database..."
docker compose up --detach --wait db

if ! docker compose exec -T db pg_restore --list < "$file" > /dev/null; then
  echo "$file is not a backup this database can read, so nothing was changed." >&2
  exit 1
fi

held=0
if [ "$(psql_db -d vrum -c "SELECT to_regclass('public.nodes') IS NOT NULL")" = "t" ]; then
  held="$(psql_db -d vrum -c "SELECT (SELECT count(*) FROM nodes) + (SELECT count(*) FROM rules) + (SELECT count(*) FROM decisions) + (SELECT count(*) FROM memories) + (SELECT count(*) FROM events)")"
fi
if [ "$held" != "0" ] && [ "$replace" != "--replace" ]; then
  echo "The database already holds data, and a restore replaces all of it. Nothing was changed." >&2
  echo "Run again with --replace if that is what you want, or restore into a fresh database:" >&2
  echo "  docker compose down --volumes   (this deletes the data)" >&2
  echo "  ./scripts/restore.sh $file" >&2
  exit 1
fi

echo "Stopping the server..."
docker compose stop app > /dev/null

echo "Restoring into a new database..."
psql_db -d postgres -c "DROP DATABASE IF EXISTS vrum_restoring WITH (FORCE)" -c "CREATE DATABASE vrum_restoring" > /dev/null
if ! docker compose exec -T db pg_restore -U vrum -d vrum_restoring --single-transaction --exit-on-error --no-owner --no-acl < "$file"; then
  echo "The backup could not be restored. The data that was there has not been touched." >&2
  psql_db -d postgres -c "DROP DATABASE IF EXISTS vrum_restoring WITH (FORCE)" > /dev/null || true
  exit 1
fi
# The event log is read in the order its transactions began. Those numbers only mean something in
# the database that wrote them, so the restored events are all given this one, in their own order.
psql_db -d vrum_restoring -c "ALTER TABLE events DISABLE TRIGGER events_are_append_only; UPDATE events SET xid = pg_current_xact_id(); ALTER TABLE events ENABLE TRIGGER events_are_append_only" > /dev/null

echo "Swapping it in..."
psql_db -d postgres \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('vrum', 'vrum_restoring') AND pid <> pg_backend_pid()" \
  -c "ALTER DATABASE vrum RENAME TO vrum_before_restore" \
  -c "ALTER DATABASE vrum_restoring RENAME TO vrum" \
  -c "DROP DATABASE vrum_before_restore WITH (FORCE)" > /dev/null

echo "Starting the server..."
if ! docker compose up --detach --build --wait; then
  echo "The data was restored, but the server did not start. If its log (docker compose logs app)" >&2
  echo "says the database was built from a different version of the schema, this backup was made" >&2
  echo "by another version of Vrum: check out the version that made it." >&2
  exit 1
fi
counts="$(psql_db -d vrum -c "SELECT (SELECT count(*) FROM nodes) || ' nodes, ' || (SELECT count(*) FROM rules) || ' rules, ' || (SELECT count(*) FROM decisions) || ' decisions, ' || (SELECT count(*) FROM memories) || ' facts and beliefs, ' || (SELECT count(*) FROM events) || ' events'")"
echo "Restored from $file: $counts."
