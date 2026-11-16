"""The PostgreSQL connection pool, building the database from its schema, and the questions the
server asks about the database.

Work on a project's graph runs in `transaction()`, which is for the project the current work was
started for (see `working_for`): the transaction switches to a database role that cannot reach
accounts, sessions or keys, and the database shows it the rows of that project and no others. With
no project named it shows none. Everything that is not a project's own work (accounts, projects,
keys, the index job, building the schema) runs in `admin_transaction()`."""

import hashlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from importlib.resources import files

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_QUICK_TIMEOUT_SECONDS = 3.0
_STARTUP_TIMEOUT_SECONDS = 30.0
_SCHEMA_LOCK_ID = 7_431_900_001

# The project the work in progress belongs to. It is set by the code that has already decided who
# may use the project (see GuardedServer and the board), never by an argument a caller sent.
_project: ContextVar[int | None] = ContextVar("vrum_project", default=None)


class NoProject(RuntimeError):
    """Work on a project's graph was started with no project named. This is a fault in the server,
    never something a caller did: the work is refused rather than run for no project."""


@contextmanager
def working_for(project_id: int) -> Iterator[None]:
    """Everything done inside this block, in tools and in what they call, is for this project."""
    token = _project.set(project_id)
    try:
        yield
    finally:
        _project.reset(token)


def current_project() -> int | None:
    return _project.get()


@dataclass(frozen=True)
class DatabaseStatus:
    postgres_version: str
    pgvector_version: str


class SchemaOutOfDate(Exception):
    """The database was built from a different schema than the one in this version of the code."""


async def prepare_schema(conninfo: str) -> None:
    """Build an empty database from `schema.sql`. A database built from any other version of the
    schema is refused in plain words: there are no migrations, so the fix is to rebuild it."""
    schema = (files("vrum") / "schema.sql").read_text(encoding="utf-8")
    digest = hashlib.sha256(schema.encode()).hexdigest()
    async with (
        await AsyncConnection.connect(conninfo, autocommit=True) as conn,
        conn.transaction(),
    ):
        # Two servers starting at once must not both build it.
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_ID,))
        cursor = await conn.execute(
            "SELECT to_regclass('schema_info') IS NOT NULL, to_regclass('nodes') IS NOT NULL"
        )
        row = await cursor.fetchone()
        assert row is not None
        has_marker, has_tables = row
        if has_marker:
            cursor = await conn.execute("SELECT schema_hash FROM schema_info")
            stored = await cursor.fetchone()
            if stored is None or stored[0] != digest:
                raise SchemaOutOfDate(_REBUILD_ADVICE)
        elif has_tables:
            raise SchemaOutOfDate(_REBUILD_ADVICE)
        else:
            await conn.execute(schema)
            await conn.execute("CREATE TABLE schema_info (schema_hash text NOT NULL)")
            await conn.execute("INSERT INTO schema_info VALUES (%s)", (digest,))


_REBUILD_ADVICE = (
    "The database was built from a different version of the schema (src/vrum/schema.sql). "
    "Vrum is a prototype and has no migrations: rebuild the database from scratch with "
    "'docker compose down --volumes' and then 'docker compose up -d --build --wait'. "
    "That deletes everything in it."
)


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

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def transaction(self, *, snapshot: bool = False) -> AsyncIterator[AsyncConnection]:
        """A connection in one transaction for the current project: what is done on it is saved
        together or not at all, and it can reach that project's rows and nothing else. With
        `snapshot` it reads one consistent view of the data, as it was when it began."""
        project = _project.get()
        if project is None:
            raise NoProject("Work on a project's graph was started with no project named.")
        async with self._pool.connection() as conn, conn.transaction():
            if snapshot:
                # The isolation level has to be chosen before the first query of the transaction.
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            await conn.execute(
                "SELECT set_config('vrum.project_id', %s, true), "
                "set_config('role', 'vrum_project', true)",
                (str(project),),
            )
            yield conn

    @asynccontextmanager
    async def admin_transaction(self) -> AsyncIterator[AsyncConnection]:
        """A connection in one transaction with no project: for accounts, projects and keys, the
        index job and building the database. Never used for a project's own work."""
        async with self._pool.connection() as conn, conn.transaction():
            yield conn

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
