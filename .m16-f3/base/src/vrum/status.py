"""What `server_status` says: the versions, how much of each kind of thing the server holds, and
the state of the index and of the services it uses. It only reads, and it never says a secret: the
language model is named, its key never, and nothing about the token or the database password."""

from datetime import datetime

from pydantic import BaseModel, Field

from vrum.common import fetch_one
from vrum.db import Database
from vrum.indexing import Indexer, IndexRun, snapshot
from vrum.search import Searcher

# How long to wait for the embedding service when only asking whether it answers.
_PROBE_SECONDS = 3.0


class ItemCounts(BaseModel):
    epics: int
    stories: int
    tasks: int
    links: int
    rules: int
    decisions: int
    principles: int
    facts: int
    beliefs: int
    events: int = Field(description="How many changes the history holds.")


class IndexStatus(BaseModel):
    meaning: str = Field(
        description="How meaning can be used now: used, not_configured (no embedding service is "
        "set up) or index_not_ready (the index is not built yet, or was built with another model). "
        "This is what every search says about the index."
    )
    note: str = Field(description="In plain words, what that means for searches.")
    waiting_to_index: int = Field(description="How many items are new or changed and not indexed.")
    indexed: int = Field(description="How many items are in the index now.")
    embedding_service: str = Field(
        description="not_configured, answering or unreachable: whether the embedding service "
        "answered a short question just now."
    )
    embedding_model: str | None = Field(description="The embedding model, if there is a service.")
    vector_size: int | None = Field(
        description="How many numbers are in each vector of the index. Empty until it is built."
    )
    last_reindex: IndexRun | None = Field(
        description="The last time the index was brought up to date since the server started, "
        "and how it went. Empty when none has run yet."
    )


class ServerStatus(BaseModel):
    vrum_version: str = Field(description="The version of this server.")
    postgres_version: str = Field(description="The version of the PostgreSQL database.")
    pgvector_version: str = Field(description="The version of the pgvector extension.")
    language_model: str | None = Field(
        description="The language model this server is set up to use for extract_facts, "
        "consolidate_memory, find_contradictions and reflect. Empty when there is none: those "
        "four tools then say so, and everything else works. Its key is never shown."
    )
    counts: ItemCounts = Field(description="How many of each kind of item the server holds.")
    index: IndexStatus
    checked_at: datetime = Field(description="When this was read.")


_COUNTS = """
SELECT
  (SELECT count(*) FROM nodes WHERE kind = 'epic') AS epics,
  (SELECT count(*) FROM nodes WHERE kind = 'story') AS stories,
  (SELECT count(*) FROM nodes WHERE kind = 'task') AS tasks,
  (SELECT count(*) FROM links) AS links,
  (SELECT count(*) FROM rules) AS rules,
  (SELECT count(*) FROM decisions WHERE kind = 'decision') AS decisions,
  (SELECT count(*) FROM decisions WHERE kind = 'principle') AS principles,
  (SELECT count(*) FROM memories WHERE type = 'fact') AS facts,
  (SELECT count(*) FROM memories WHERE type = 'belief') AS beliefs,
  (SELECT count(*) FROM events) AS events,
  now() AS checked_at
"""


class Status:
    def __init__(
        self, database: Database, indexer: Indexer, searcher: Searcher, language_model: str | None
    ) -> None:
        self._database = database
        self._indexer = indexer
        self._searcher = searcher
        self._language_model = language_model

    async def server_status(self, vrum_version: str) -> ServerStatus:
        versions = await self._database.status()
        client = self._indexer.client
        async with self._database.transaction() as conn:
            row = await fetch_one(conn, _COUNTS)
            state = await snapshot(conn, client.model if client else None)
            built = await fetch_one(conn, "SELECT to_regclass('semantic_index') IS NOT NULL AS yes")
            assert row is not None and built is not None
            indexed = (
                await fetch_one(conn, "SELECT count(*) AS n FROM semantic_index")
                if built["yes"]
                else None
            )
        note = await self._searcher.index_state()
        return ServerStatus(
            vrum_version=vrum_version,
            postgres_version=versions.postgres_version,
            pgvector_version=versions.pgvector_version,
            language_model=self._language_model,
            counts=ItemCounts(**{name: int(row[name]) for name in ItemCounts.model_fields}),
            index=IndexStatus(
                meaning=note.meaning,
                note=note.note,
                waiting_to_index=note.waiting_to_index,
                indexed=int(indexed["n"]) if indexed else 0,
                embedding_service=await self._indexer.probe(_PROBE_SECONDS),
                embedding_model=client.model if client else None,
                vector_size=state.dimensions,
                last_reindex=self._indexer.last_run,
            ),
            checked_at=row["checked_at"],
        )
