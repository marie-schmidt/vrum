"""The PostgreSQL connection pool, the schema migrations, and the questions the server asks about
the database."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.resources import files

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_QUICK_TIMEOUT_SECONDS = 3.0
_STARTUP_TIMEOUT_SECONDS = 30.0
_MIGRATION_LOCK_ID = 7_431_900_001


@dataclass(frozen=True)
class DatabaseStatus:
    postgres_version: str
    pgvector_version: str


class Database:
    def __init__(self, conninfo: str) -> None:
        self._pool = AsyncConnectionPool(
            conninfo,
            min_size=1,
            max_size=10,
            open=False,
            timeout=5.0,
            kwargs={"autocommit": True},
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=_STARTUP_TIMEOUT_SECONDS)
        async with self._pool.connection() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await self._migrate()

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """A connection in one transaction: what is done on it is saved together or not at all."""
        async with self._pool.connection() as conn, conn.transaction():
            yield conn

    async def _migrate(self) -> None:
        """Apply each numbered file in `migrations/` not yet applied, once, in order."""
        migrations = sorted(
            (
                path
                for path in (files("vrum") / "migrations").iterdir()
                if path.name.endswith(".sql")
            ),
            key=lambda path: path.name,
        )
        async with self.transaction() as conn:
            # Two servers starting at once must not apply the same migration twice.
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_ID,))
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cursor = await conn.execute("SELECT name FROM schema_migrations")
            applied = {row[0] for row in await cursor.fetchall()}
            for path in migrations:
                if path.name in applied:
                    continue
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))

    async def ping(self) -> None:
        """Return if the database answers within a few seconds; raise otherwise."""
        async with self._pool.connection(timeout=_QUICK_TIMEOUT_SECONDS) as conn:
            await conn.execute("SELECT 1")

    async def status(self) -> DatabaseStatus:
        async with (
            self._pool.connection(timeout=_QUICK_TIMEOUT_SECONDS) as conn,
            conn.cursor(row_factory=dict_row) as cursor,
        ):
            await cursor.execute(
                "SELECT current_setting('server_version') AS postgres_version, "
                "(SELECT extversion FROM pg_extension WHERE extname = 'vector') "
                "AS pgvector_version"
            )
            row = await cursor.fetchone()
        if row is None or row["pgvector_version"] is None:
            raise RuntimeError("The database did not report its version or the pgvector extension.")
        return DatabaseStatus(
            postgres_version=row["postgres_version"], pgvector_version=row["pgvector_version"]
        )
