"""The semantic index: embeddings of epics, stories, tasks, acceptance criteria and notes. It is
derived from the graph, can be dropped at any time, and never decides anything.

This module is the only one that writes to the index tables (it also creates and drops them): the
reindex job below does it, on request and in the background. Everything else only reads them.

One index holds every project's items, each with its project, and a project's work sees only its
own rows of it (the database enforces that, as it does for the graph). The job that keeps it
current works for every project, so it runs with no project named; a request to reindex is for one
project and reports on that project.

An item is kept in the index under its type and one number: an epic, story or task by its number
within its kind (unique, since the type says which kind), a rule or note by its
id."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import Refusal, fetch_all, fetch_one
from vrum.config import EmbeddingSettings
from vrum.db import Database, working_for
from vrum.embeddings import (
    BATCH_SIZE,
    EmbeddingClient,
    EmbeddingRejected,
    EmbeddingUnavailable,
    vector_literal,
)
from vrum.items import NamedItem, named

log = logging.getLogger("vrum.indexing")

# The types of thing that are embedded.
INDEXED_TYPES = ("epic", "story", "task", "rule", "note")
# The biggest vector PostgreSQL's vector type can hold.
_MOST_DIMENSIONS = 16000


@dataclass(frozen=True)
class IndexSnapshot:
    """What the index is right now: whether it exists, what it was built with, and how many
    items are waiting to be indexed (new or changed since they were embedded)."""

    built: bool
    model: str | None
    dimensions: int | None
    waiting: int

    def usable_with(self, model: str) -> bool:
        return self.built and self.model == model


async def _index_info(conn: AsyncConnection) -> dict[str, object] | None:
    """The index table's model and dimensions when both index tables exist, else None."""
    exists = await fetch_one(
        conn,
        "SELECT to_regclass('semantic_index') IS NOT NULL "
        "AND to_regclass('semantic_index_info') IS NOT NULL AS built",
    )
    assert exists is not None
    if not exists["built"]:
        return None
    return await fetch_one(conn, "SELECT model, dimensions FROM semantic_index_info")


async def _watermark(
    conn: AsyncConnection, model: str
) -> tuple[str, int, int, int, int, int, int, int] | None:
    """Cheap digest of whether any embeddable row or the index changed, avoiding full-body MD5
    hashing across all projects on idle ticks. Every update to nodes/rules increments version (by
    trigger) and notes are immutable."""
    info = await _index_info(conn)
    if info is None or info["model"] != model:
        return None
    row = await fetch_one(
        conn,
        "SELECT (SELECT count(*) FROM semantic_index) AS indexed_n, "
        "(SELECT count(*) FROM nodes) AS nodes_n, "
        "(SELECT coalesce(sum(version), 0) FROM nodes) AS nodes_v, "
        "(SELECT count(*) FROM rules) AS rules_n, "
        "(SELECT coalesce(sum(version), 0) FROM rules) AS rules_v, "
        "(SELECT count(*) FROM notes) AS notes_n, "
        "(SELECT coalesce(max(id), 0) FROM notes) AS notes_id",
    )
    assert row is not None
    return (
        str(info["model"]),
        int(row["indexed_n"]),
        int(row["nodes_n"]),
        int(row["nodes_v"]),
        int(row["rules_n"]),
        int(row["rules_v"]),
        int(row["notes_n"]),
        int(row["notes_id"]),
    )


async def snapshot(conn: AsyncConnection, model: str | None) -> IndexSnapshot:
    """Read the state of the index. With no embedding service configured (`model` is None) the
    index is not built and nothing is waiting, because nothing could be done about it."""
    if model is None:
        return IndexSnapshot(built=False, model=None, dimensions=None, waiting=0)
    info = await _index_info(conn)
    if info is None or info["model"] != model:
        # Nothing built with this model yet, so every embeddable item is waiting.
        row = await fetch_one(conn, "SELECT count(*) AS waiting FROM index_source")
        built = info is not None
    else:
        row = await fetch_one(
            conn,
            "SELECT count(*) AS waiting FROM index_source s WHERE NOT EXISTS ("
            "SELECT 1 FROM semantic_index i WHERE i.project_id = s.project_id "
            "AND i.item_type = s.item_type "
            "AND i.item_id = s.id AND i.content_hash = s.content_hash)",
        )
        built = True
    assert row is not None
    return IndexSnapshot(
        built=built,
        model=str(info["model"]) if info else None,
        dimensions=int(str(info["dimensions"])) if info else None,
        waiting=int(row["waiting"]),
    )


class FailedItem(NamedItem):
    reason: str


# How many of the items the embedding service refused one reindex report lists.
MOST_FAILED_SHOWN = 50


class IndexRun(BaseModel):
    """How the last time the index was brought up to date went."""

    at: datetime = Field(description="When it finished.")
    embedded: int = Field(description="How many items it embedded.")
    failed_total: int = Field(description="How many items the embedding service refused.")
    waiting_after: int | None = Field(
        description="How many items were still waiting afterwards. Empty when it stopped before "
        "it could count."
    )
    error: str | None = Field(
        description="Why it stopped early, in words (the service could not be reached). Empty when "
        "it went through."
    )
    seconds: float = Field(description="How long it took.")


class IndexReport(BaseModel):
    model: str = Field(description="The embedding model the index is built with.")
    dimensions: int = Field(description="The size of its vectors, read from the model.")
    embedded: int = Field(description="How many items were embedded in this run.")
    failed: list[FailedItem] = Field(
        description="Items the service refused (for example, text it cannot take), the first "
        f"{MOST_FAILED_SHOWN} of them. The rest were still indexed. A failed item stays waiting "
        "until its text changes."
    )
    failed_total: int = Field(description="How many items the service refused in this run.")
    waiting_after: int = Field(description="How many items are still waiting to be indexed.")
    indexed_total: int = Field(description="How many items are in the index now.")
    rebuilt_because: str | None = Field(
        description="Set when the whole index was built from nothing in this run, and why."
    )
    error: str | None = Field(
        description="Set when the run stopped early because the service could not be reached. "
        "What was embedded before that is kept; run it again later for the rest."
    )
    seconds: float = Field(description="How long the run took.")


class Indexer:
    def __init__(self, database: Database, settings: EmbeddingSettings | None) -> None:
        self._database = database
        self._settings = settings
        self._client = EmbeddingClient(settings) if settings else None
        # Passes run one at a time: a request for a reindex waits for the one already running.
        self._lock = asyncio.Lock()
        self._last_run: IndexRun | None = None
        self._clean_mark: tuple[str, int, int, int, int, int, int, int] | None = None

    @property
    def client(self) -> EmbeddingClient | None:
        return self._client

    @property
    def last_run(self) -> IndexRun | None:
        """How the last reindex since the server started went, for the status page."""
        return self._last_run

    async def probe(self, timeout: float) -> str:
        """Whether the embedding service answers a short question: not_configured, answering or
        unreachable. A service that refuses the text still answers."""
        if self._client is None:
            return "not_configured"
        try:
            await self._client.embed(["status check"], timeout=timeout)
        except EmbeddingRejected:
            return "answering"
        except EmbeddingUnavailable:
            return "unreachable"
        return "answering"

    @property
    def max_chars(self) -> int:
        assert self._settings is not None
        return self._settings.max_chars

    def require_client(self) -> EmbeddingClient:
        if self._client is None:
            raise Refusal(
                "No embedding service is set up, so there is no index to build. Set "
                "EMBEDDING_BASE_URL and EMBEDDING_MODEL (see AGENTS.md) and restart. Searching by "
                "exact words works without one."
            )
        return self._client

    @asynccontextmanager
    async def _reading(self, project_id: int | None) -> AsyncIterator[AsyncConnection]:
        """A transaction for reading the index: one project's rows, or with no project every
        project's (for the job that serves them all)."""
        if project_id is None:
            async with self._database.admin_transaction() as conn:
                yield conn
        else:
            with working_for(project_id):
                async with self._database.transaction() as conn:
                    yield conn

    async def reindex(self, project_id: int | None = None) -> IndexReport:
        """Bring the index up to date: build it if it does not exist (or was built with another
        model), then embed every item that is new or changed, in one project or, with none named,
        in all of them. Raises EmbeddingUnavailable when the service cannot be reached at the
        start; later trouble is reported in the result. Either way, how it went is kept for the
        status page."""
        started = time.monotonic()
        try:
            report = await self._reindex(started, project_id)
        except EmbeddingUnavailable as error:
            self._remember(0, 0, None, str(error), started)
            raise
        self._remember(
            report.embedded, report.failed_total, report.waiting_after, report.error, started
        )
        return report

    def _remember(
        self, embedded: int, failed: int, waiting: int | None, error: str | None, started: float
    ) -> None:
        self._last_run = IndexRun(
            at=datetime.now(UTC),
            embedded=embedded,
            failed_total=failed,
            waiting_after=waiting,
            error=error,
            seconds=round(time.monotonic() - started, 3),
        )

    async def _reindex(self, started: float, project_id: int | None) -> IndexReport:
        client = self.require_client()
        assert self._settings is not None
        async with self._lock:
            self._clean_mark = None
            probe = await client.embed(["dimension probe"])
            dimensions = len(probe[0])
            if dimensions > _MOST_DIMENSIONS:
                raise Refusal(
                    f"The model {client.model} gives vectors of {dimensions} numbers, and "
                    f"PostgreSQL's vector type holds at most {_MOST_DIMENSIONS}."
                )
            rebuilt_because = await self._ensure_tables(client.model, dimensions)
            embedded, failed, error = await self._embed_waiting(
                client, self._settings.max_chars, project_id
            )
            async with self._reading(project_id) as conn:
                state = await snapshot(conn, client.model)
                total = await fetch_one(conn, "SELECT count(*) AS total FROM semantic_index")
            assert total is not None
        return IndexReport(
            model=client.model,
            dimensions=dimensions,
            embedded=embedded,
            failed=failed[:MOST_FAILED_SHOWN],
            failed_total=len(failed),
            waiting_after=state.waiting,
            indexed_total=int(total["total"]),
            rebuilt_because=rebuilt_because,
            error=error,
            seconds=round(time.monotonic() - started, 3),
        )

    async def _ensure_tables(self, model: str, dimensions: int) -> str | None:
        """Make sure the index tables exist for this model and vector size, dropping and
        rebuilding them if they were built for another. Says why if it built them."""
        async with self._database.admin_transaction() as conn:
            info = await _index_info(conn)
            if info is not None and (info["model"], info["dimensions"]) == (model, dimensions):
                return None
            if info is None:
                reason = "The index did not exist, so it was built."
            elif info["model"] != model:
                reason = (
                    f"The index was built with the model {info['model']} and the model is now "
                    f"{model}, so it was rebuilt."
                )
            else:
                reason = (
                    f"The model's vectors are now {dimensions} numbers long and were "
                    f"{info['dimensions']}, so the index was rebuilt."
                )
            # Readers look at the info table first and the index second; dropping in the
            # same order keeps a rebuild from deadlocking with a search that is reading.
            await conn.execute("DROP TABLE IF EXISTS semantic_index_info, semantic_index")
            # `dimensions` is a whole number read from the service and checked above.
            await conn.execute(
                "CREATE TABLE semantic_index ("
                "project_id bigint NOT NULL REFERENCES projects (id), "
                "item_type text NOT NULL, item_id bigint NOT NULL, content_hash text NOT NULL, "
                f"embedding vector({int(dimensions)}) NOT NULL, "
                "indexed_at timestamptz NOT NULL DEFAULT now(), "
                "PRIMARY KEY (project_id, item_type, item_id))"
            )
            await conn.execute(
                "CREATE TABLE semantic_index_info ("
                "only_row boolean PRIMARY KEY DEFAULT true CHECK (only_row), "
                "model text NOT NULL, dimensions integer NOT NULL, "
                "built_at timestamptz NOT NULL DEFAULT now())"
            )
            await conn.execute(
                "INSERT INTO semantic_index_info (model, dimensions) VALUES (%s, %s)",
                (model, dimensions),
            )
            # A project's work reads its own rows of the index and no others, and writes none.
            await conn.execute("ALTER TABLE semantic_index ENABLE ROW LEVEL SECURITY")
            await conn.execute(
                "CREATE POLICY project_only ON semantic_index "
                "USING (project_id = current_project())"
            )
            await conn.execute(
                "GRANT SELECT ON semantic_index, semantic_index_info TO vrum_project"
            )
            return reason

    async def _embed_waiting(
        self, client: EmbeddingClient, max_chars: int, project_id: int | None
    ) -> tuple[int, list[FailedItem], str | None]:
        embedded = 0
        failed: list[FailedItem] = []
        # Items are taken in order, past the last one tried, so one that keeps failing cannot hold
        # up the rest or be tried over and over in one run.
        after: tuple[int, str, int] = (0, "", 0)
        while True:
            async with self._database.admin_transaction() as conn:
                batch = await fetch_all(
                    conn,
                    "SELECT s.project_id, s.item_type, s.id, s.content, s.content_hash "
                    "FROM index_source s LEFT JOIN semantic_index i "
                    "ON i.project_id = s.project_id AND i.item_type = s.item_type "
                    "AND i.item_id = s.id "
                    "WHERE i.content_hash IS DISTINCT FROM s.content_hash "
                    "AND (%s::bigint IS NULL OR s.project_id = %s) "
                    "AND (s.project_id, s.item_type, s.id) > (%s, %s, %s) "
                    "ORDER BY s.project_id, s.item_type, s.id LIMIT %s",
                    (project_id, project_id, after[0], after[1], after[2], BATCH_SIZE),
                )
            if not batch:
                return embedded, failed, None
            after = (batch[-1]["project_id"], batch[-1]["item_type"], batch[-1]["id"])
            try:
                done, refused = await _embed_batch(client, batch, max_chars)
            except EmbeddingUnavailable as error:
                return embedded, failed, str(error)
            failed.extend(refused)
            if done:
                async with self._database.admin_transaction() as conn, conn.cursor() as cursor:
                    await cursor.executemany(
                        "INSERT INTO semantic_index (project_id, item_type, item_id, "
                        "content_hash, embedding) VALUES (%s, %s, %s, %s, %s::vector) "
                        "ON CONFLICT (project_id, item_type, item_id) DO UPDATE SET "
                        "content_hash = EXCLUDED.content_hash, "
                        "embedding = EXCLUDED.embedding, indexed_at = now()",
                        [
                            (
                                row["project_id"],
                                row["item_type"],
                                row["id"],
                                row["content_hash"],
                                vector,
                            )
                            for row, vector in done
                        ],
                    )
                embedded += len(done)

    async def run_forever(self) -> None:
        """The background job: whenever items are waiting to be indexed, in any project, index
        them. It looks again after a few seconds, and waits longer after trouble. It stops when
        cancelled."""
        assert self._settings is not None and self._client is not None
        interval = self._settings.reindex_seconds
        after_trouble = max(interval, min(interval * 5, 30.0))
        in_outage = False
        while True:
            delay = interval
            try:
                async with self._reading(None) as conn:
                    mark = await _watermark(conn, self._client.model)
                    if mark is not None and mark == self._clean_mark:
                        waiting = 0
                    else:
                        waiting = (await snapshot(conn, self._client.model)).waiting
                        self._clean_mark = mark if waiting == 0 else None
                if waiting:
                    report = await self.reindex()
                    if report.error or report.failed_total:
                        delay = after_trouble
                    else:
                        in_outage = False
            except EmbeddingUnavailable as error:
                if not in_outage:
                    log.warning("Indexing is waiting for the embedding service: %s", error)
                    in_outage = True
                delay = after_trouble
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("The reindex job hit an unexpected error and will try again.")
                delay = after_trouble
            await asyncio.sleep(delay)


async def _embed_batch(
    client: EmbeddingClient, batch: list[dict[str, object]], max_chars: int
) -> tuple[list[tuple[dict[str, object], str]], list[FailedItem]]:
    """Embed a batch. If the service refuses the batch, try the items one at a time to find the
    one it refuses, so that it does not stop the others. Returns what was embedded (with the
    vector as PostgreSQL reads it) and what was refused."""
    texts = [str(row["content"])[:max_chars] for row in batch]
    try:
        vectors = await client.embed(texts)
        return [
            (row, vector_literal(vector)) for row, vector in zip(batch, vectors, strict=True)
        ], []
    except EmbeddingRejected:
        pass
    done: list[tuple[dict[str, object], str]] = []
    failed: list[FailedItem] = []
    for row, text in zip(batch, texts, strict=True):
        try:
            (vector,) = await client.embed([text])
            done.append((row, vector_literal(vector)))
        except EmbeddingRejected as error:
            failed.append(
                FailedItem(**named(str(row["item_type"]), int(str(row["id"]))), reason=str(error))
            )
    return done, failed
