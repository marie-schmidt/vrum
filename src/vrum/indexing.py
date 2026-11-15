"""The semantic index: embeddings of epics, stories, rules, decisions, principles, facts and
beliefs. It is derived from the graph, can be dropped at any time, and never decides anything.

This module is the only one that writes to the index tables (it also creates and drops them): the
reindex job below does it, on request and in the background. Everything else only reads them."""

import asyncio
import logging
import time
from dataclasses import dataclass

from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from vrum.common import Refusal, fetch_all, fetch_one
from vrum.config import EmbeddingSettings
from vrum.db import Database
from vrum.embeddings import (
    BATCH_SIZE,
    EmbeddingClient,
    EmbeddingRejected,
    EmbeddingUnavailable,
    vector_literal,
)

log = logging.getLogger("vrum.indexing")

# The types of thing that are embedded. A task is found by exact words only.
INDEXED_TYPES = ("epic", "story", "rule", "decision", "principle", "fact", "belief")
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


async def snapshot(conn: AsyncConnection, model: str | None) -> IndexSnapshot:
    """Read the state of the index. With no embedding service configured (`model` is None) the
    index is not built and nothing is waiting, because nothing could be done about it."""
    if model is None:
        return IndexSnapshot(built=False, model=None, dimensions=None, waiting=0)
    exists = await fetch_one(
        conn,
        "SELECT to_regclass('semantic_index') IS NOT NULL "
        "AND to_regclass('semantic_index_info') IS NOT NULL AS built",
    )
    assert exists is not None
    info = (
        await fetch_one(conn, "SELECT model, dimensions FROM semantic_index_info")
        if exists["built"]
        else None
    )
    if info is None or info["model"] != model:
        # Nothing built with this model yet, so every embeddable item is waiting.
        row = await fetch_one(conn, "SELECT count(*) AS waiting FROM index_source")
        built = info is not None
    else:
        row = await fetch_one(
            conn,
            "SELECT count(*) AS waiting FROM index_source s WHERE NOT EXISTS ("
            "SELECT 1 FROM semantic_index i WHERE i.item_type = s.item_type "
            "AND i.item_id = s.id AND i.content_hash = s.content_hash)",
        )
        built = True
    assert row is not None
    return IndexSnapshot(
        built=built,
        model=info["model"] if info else None,
        dimensions=info["dimensions"] if info else None,
        waiting=int(row["waiting"]),
    )


class FailedItem(BaseModel):
    item_type: str
    id: int
    reason: str


# How many of the items the embedding service refused one reindex report lists.
MOST_FAILED_SHOWN = 50


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

    @property
    def client(self) -> EmbeddingClient | None:
        return self._client

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

    async def reindex(self) -> IndexReport:
        """Bring the index up to date: build it if it does not exist (or was built with another
        model), then embed every item that is new or changed. Raises EmbeddingUnavailable when
        the service cannot be reached at the start; later trouble is reported in the result."""
        client = self.require_client()
        assert self._settings is not None
        started = time.monotonic()
        async with self._lock:
            probe = await client.embed(["dimension probe"])
            dimensions = len(probe[0])
            if dimensions > _MOST_DIMENSIONS:
                raise Refusal(
                    f"The model {client.model} gives vectors of {dimensions} numbers, and "
                    f"PostgreSQL's vector type holds at most {_MOST_DIMENSIONS}."
                )
            rebuilt_because = await self._ensure_tables(client.model, dimensions)
            embedded, failed, error = await self._embed_waiting(client, self._settings.max_chars)
            async with self._database.transaction() as conn:
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
        async with self._database.transaction() as conn:
            exists = await fetch_one(
                conn,
                "SELECT to_regclass('semantic_index') IS NOT NULL "
                "AND to_regclass('semantic_index_info') IS NOT NULL AS built",
            )
            assert exists is not None
            info = (
                await fetch_one(conn, "SELECT model, dimensions FROM semantic_index_info")
                if exists["built"]
                else None
            )
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
            await conn.execute("DROP TABLE IF EXISTS semantic_index, semantic_index_info")
            # `dimensions` is a whole number read from the service and checked above.
            await conn.execute(
                "CREATE TABLE semantic_index ("
                "item_type text NOT NULL, item_id bigint NOT NULL, content_hash text NOT NULL, "
                f"embedding vector({int(dimensions)}) NOT NULL, "
                "indexed_at timestamptz NOT NULL DEFAULT now(), "
                "PRIMARY KEY (item_type, item_id))"
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
            return reason

    async def _embed_waiting(
        self, client: EmbeddingClient, max_chars: int
    ) -> tuple[int, list[FailedItem], str | None]:
        embedded = 0
        failed: list[FailedItem] = []
        # Items are taken in order, past the last one tried, so one that keeps failing cannot hold
        # up the rest or be tried over and over in one run.
        after: tuple[str, int] = ("", 0)
        while True:
            async with self._database.transaction() as conn:
                batch = await fetch_all(
                    conn,
                    "SELECT s.item_type, s.id, s.content, s.content_hash FROM index_source s "
                    "LEFT JOIN semantic_index i ON i.item_type = s.item_type AND i.item_id = s.id "
                    "WHERE i.content_hash IS DISTINCT FROM s.content_hash "
                    "AND (s.item_type, s.id) > (%s, %s) ORDER BY s.item_type, s.id LIMIT %s",
                    (after[0], after[1], BATCH_SIZE),
                )
            if not batch:
                return embedded, failed, None
            after = (batch[-1]["item_type"], batch[-1]["id"])
            try:
                done, refused = await _embed_batch(client, batch, max_chars)
            except EmbeddingUnavailable as error:
                return embedded, failed, str(error)
            failed.extend(refused)
            if done:
                async with self._database.transaction() as conn, conn.cursor() as cursor:
                    await cursor.executemany(
                        "INSERT INTO semantic_index (item_type, item_id, content_hash, "
                        "embedding) VALUES (%s, %s, %s, %s::vector) "
                        "ON CONFLICT (item_type, item_id) DO UPDATE SET "
                        "content_hash = EXCLUDED.content_hash, "
                        "embedding = EXCLUDED.embedding, indexed_at = now()",
                        [
                            (row["item_type"], row["id"], row["content_hash"], vector)
                            for row, vector in done
                        ],
                    )
                embedded += len(done)

    async def run_forever(self) -> None:
        """The background job: whenever items are waiting to be indexed, index them. It looks
        again after a few seconds, and waits longer after trouble. It stops when cancelled."""
        assert self._settings is not None and self._client is not None
        interval = self._settings.reindex_seconds
        after_trouble = max(interval, min(interval * 5, 30.0))
        in_outage = False
        while True:
            delay = interval
            try:
                async with self._database.transaction() as conn:
                    waiting = (await snapshot(conn, self._client.model)).waiting
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
                FailedItem(
                    item_type=str(row["item_type"]), id=int(str(row["id"])), reason=str(error)
                )
            )
    return done, failed
