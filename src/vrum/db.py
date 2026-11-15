"""The PostgreSQL connection pool, and the questions the server asks about the database."""

from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool

_QUICK_TIMEOUT_SECONDS = 3.0
_STARTUP_TIMEOUT_SECONDS = 30.0


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
            kwargs={"autocommit": True},
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=_STARTUP_TIMEOUT_SECONDS)
        async with self._pool.connection() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

    async def close(self) -> None:
        await self._pool.close()

    async def ping(self) -> None:
        """Return if the database answers within a few seconds; raise otherwise."""
        async with self._pool.connection(timeout=_QUICK_TIMEOUT_SECONDS) as conn:
            await conn.execute("SELECT 1")

    async def status(self) -> DatabaseStatus:
        async with self._pool.connection(timeout=_QUICK_TIMEOUT_SECONDS) as conn:
            version_row = await (await conn.execute("SHOW server_version")).fetchone()
            vector_row = await (
                await conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).fetchone()
        if version_row is None or vector_row is None:
            raise RuntimeError("The database did not report its version or the pgvector extension.")
        return DatabaseStatus(postgres_version=version_row[0], pgvector_version=vector_row[0])
