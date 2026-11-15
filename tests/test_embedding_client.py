"""The embedding client against a real HTTP server on this machine, so what it does with each kind
of answer (and with no answer at all) is seen as it really happens."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from vrum.config import EmbeddingSettings
from vrum.embeddings import (
    EmbeddingClient,
    EmbeddingRejected,
    EmbeddingUnavailable,
    vector_literal,
)

pytestmark = pytest.mark.anyio


class _Service:
    """What the fake service does next, and what it was sent."""

    def __init__(self) -> None:
        self.status = 200
        self.body: bytes | None = None
        self.sent: list[tuple[dict[str, str], Any]] = []
        self.port = 0


@pytest.fixture
def service() -> Iterator[_Service]:
    state = _Service()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.sent.append((dict(self.headers), request))
            body = state.body
            if body is None:
                body = json.dumps(
                    {
                        "data": [
                            {"index": index, "embedding": [float(len(text)), 1.0]}
                            for index, text in enumerate(request["input"])
                        ]
                    }
                ).encode()
            self.send_response(state.status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    state.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def client_for(port: int, api_key: str | None = None) -> EmbeddingClient:
    return EmbeddingClient(
        EmbeddingSettings(
            base_url=f"http://127.0.0.1:{port}/v1",
            model="a-model",
            api_key=api_key,
            max_chars=2000,
            min_similarity=0.35,
            overlap_threshold=0.55,
            reindex_seconds=5.0,
        )
    )


async def test_it_sends_the_model_and_the_texts_and_gives_back_one_vector_each(
    service: _Service,
) -> None:
    vectors = await client_for(service.port).embed(["one", "three"])

    assert vectors == [[3.0, 1.0], [5.0, 1.0]]
    ((headers, request),) = service.sent
    assert request == {"model": "a-model", "input": ["one", "three"]}
    assert "Authorization" not in headers


async def test_it_sends_the_key_when_there_is_one(service: _Service) -> None:
    await client_for(service.port, api_key="the-key").embed(["x"])
    assert service.sent[0][0]["Authorization"] == "Bearer the-key"


async def test_vectors_are_put_back_in_the_order_of_the_texts(service: _Service) -> None:
    service.body = json.dumps(
        {
            "data": [
                {"index": 1, "embedding": [2.0, 2.0]},
                {"index": 0, "embedding": [1.0, 1.0]},
            ]
        }
    ).encode()
    assert await client_for(service.port).embed(["a", "b"]) == [[1.0, 1.0], [2.0, 2.0]]


async def test_no_texts_means_no_request(service: _Service) -> None:
    assert await client_for(service.port).embed([]) == []
    assert service.sent == []


@pytest.mark.parametrize("status", [400, 413, 422])
async def test_a_service_that_refuses_the_text_is_said_to_have_refused_it(
    service: _Service, status: int
) -> None:
    service.status = status
    service.body = b'{"error": "too long"}'
    with pytest.raises(EmbeddingRejected, match=f"refused the text \\({status}: .*too long"):
        await client_for(service.port).embed(["x"])


@pytest.mark.parametrize("status", [401, 404, 500, 503])
async def test_a_service_that_cannot_answer_is_unavailable_with_advice(
    service: _Service, status: int
) -> None:
    service.status = status
    service.body = b"nope"
    with pytest.raises(EmbeddingUnavailable, match=f"answered {status}.*EMBEDDING_MODEL"):
        await client_for(service.port).embed(["x"])


async def test_a_service_that_is_not_there_is_unavailable() -> None:
    # Nothing listens on port 9 on a normal machine, so the connection is refused.
    with pytest.raises(EmbeddingUnavailable, match="cannot be reached"):
        await client_for(9).embed(["x"])


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",
        b'{"data": "no"}',
        b'{"data": [{"index": 0}]}',
        b'{"data": []}',
        b'{"data": [{"index": 0, "embedding": []}]}',
        b'{"data": [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}]}',
    ],
)
async def test_an_answer_that_is_not_what_an_embedding_service_says_is_unavailable(
    service: _Service, body: bytes
) -> None:
    service.body = body
    with pytest.raises(EmbeddingUnavailable):
        await client_for(service.port).embed(["a", "b"])


def test_a_vector_is_written_the_way_the_database_reads_it() -> None:
    assert vector_literal([1, 0.5, -0.25]) == "[1.0,0.5,-0.25]"
