"""The embedding client: the one small interface to an OpenAI-compatible embedding service.

It only turns text into vectors. It knows nothing about the graph or the index."""

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Sequence

from vrum.config import EmbeddingSettings

_TIMEOUT_SECONDS = 60.0
# Texts sent in one request. Small enough for any service, large enough to be quick.
BATCH_SIZE = 32


class EmbeddingUnavailable(Exception):
    """The service cannot be reached, or failed. Trying again later may work."""


class EmbeddingRejected(Exception):
    """The service refused this input (for example, it is too long). Trying again changes
    nothing: the input has to change."""


def vector_literal(vector: Sequence[float]) -> str:
    """A vector written the way PostgreSQL's vector type reads it."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


class EmbeddingClient:
    def __init__(self, settings: EmbeddingSettings) -> None:
        self._settings = settings

    @property
    def model(self) -> str:
        return self._settings.model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in order. Raises EmbeddingUnavailable or EmbeddingRejected."""
        if not texts:
            return []
        return await asyncio.to_thread(self._embed_now, list(texts))

    def _embed_now(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self._settings.api_key:
            headers["Authorization"] = f"Bearer {self._settings.api_key}"
        request = urllib.request.Request(
            f"{self._settings.base_url}/embeddings",
            data=json.dumps({"model": self._settings.model, "input": texts}).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            detail = _detail(error)
            # A client error means this input was refused. A server error, or a missing or
            # forbidden address, means the service is not in a state to answer anyone.
            if error.code in (400, 413, 422):
                raise EmbeddingRejected(
                    f"The embedding service refused the text ({error.code}{detail})."
                ) from error
            raise EmbeddingUnavailable(
                f"The embedding service at {self._settings.base_url} answered {error.code}"
                f"{detail}. Check EMBEDDING_BASE_URL, EMBEDDING_MODEL and EMBEDDING_API_KEY."
            ) from error
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise EmbeddingUnavailable(
                f"The embedding service at {self._settings.base_url} cannot be reached ({reason})."
            ) from error
        return _vectors(body, len(texts), self._settings.base_url)


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        text = error.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""
    return f": {text[:200]}" if text else ""


def _vectors(body: bytes, expected: int, base_url: str) -> list[list[float]]:
    try:
        rows = json.loads(body)["data"]
        ordered = sorted(rows, key=lambda row: row["index"])
        vectors = [[float(value) for value in row["embedding"]] for row in ordered]
    except (ValueError, KeyError, TypeError) as error:
        raise EmbeddingUnavailable(
            f"The embedding service at {base_url} did not answer the way an OpenAI-compatible "
            "embedding service does (a list called data, with an embedding for each input)."
        ) from error
    if len(vectors) != expected or len({len(vector) for vector in vectors}) != 1 or not vectors[0]:
        raise EmbeddingUnavailable(
            f"The embedding service at {base_url} returned {len(vectors)} vectors for {expected} "
            "texts, or vectors of different sizes."
        )
    return vectors
