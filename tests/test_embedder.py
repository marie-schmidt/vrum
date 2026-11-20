"""The embedding service of the default stack (embedder/server.py), tried without its model: how it
reads a request, what it refuses and in what words, the shape of what it answers, that it never
logs or tells a text it was sent, and that compose.yaml and the image agree with it (the model's
name, the address and port, the checksums). The model itself is proved by
tests/test_embeddings_second_stack.py, which starts the real thing."""

import importlib.util
import json
import re
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
EMBEDDER = ROOT / "embedder"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("embedder_server", EMBEDDER / "server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["embedder_server"] = module
    spec.loader.exec_module(module)
    return module


server = _load()
MODEL = "all-MiniLM-L6-v2"


def fake_embed(texts: Sequence[str]) -> tuple[list[list[float]], int]:
    """Two numbers for each text: how long it is, and one. Enough to see order and count."""
    return [[float(len(text)), 1.0] for text in texts], sum(len(text.split()) for text in texts)


def body(**request: Any) -> bytes:
    return json.dumps({"model": MODEL, **request}).encode()


def refusal_of(raw: bytes) -> Any:
    with pytest.raises(server.Refused) as caught:
        server.texts_in(raw)
    return caught.value


def test_one_text_or_a_list_of_texts_is_read_in_order() -> None:
    assert server.texts_in(body(input="one text")) == ["one text"]
    assert server.texts_in(body(input=["a", "b", "c"])) == ["a", "b", "c"]
    assert server.texts_in(body(input=["a"], encoding_format="float", dimensions=384)) == ["a"]


@pytest.mark.parametrize(
    ("raw", "status", "words"),
    [
        (b"not json", 400, "not valid JSON"),
        (b"[1, 2]", 400, "must be a JSON object"),
        (
            body(model="gpt-embed", input="x").replace(MODEL.encode(), b"gpt-embed"),
            400,
            "gpt-embed",
        ),
        (json.dumps({"input": "x"}).encode(), 400, "asked for none"),
        (body(), 400, "needs an input"),
        (body(input=["a", 7]), 400, "a text, or a list of texts"),
        (body(input={"a": 1}), 400, "a text, or a list of texts"),
        (body(input=[]), 400, "nothing to embed"),
        (body(input=["x"] * 129), 400, "At most 128 texts"),
        (body(input="x", encoding_format="base64"), 400, "float"),
        (body(input="x", dimensions=64), 400, "384 numbers"),
        (b"x" * (4 * 1024 * 1024 + 1), 413, "larger than 4 MiB"),
    ],
)
def test_a_request_that_cannot_be_answered_is_refused_in_plain_words(
    raw: bytes, status: int, words: str
) -> None:
    refusal = refusal_of(raw)
    assert refusal.status == status
    assert words in refusal.message
    # A person reading it is told what to do, or what was wrong, and nothing from the request.
    assert refusal.message.endswith(".")


def test_a_wrong_model_is_told_which_one_the_service_has_and_what_to_set() -> None:
    message = refusal_of(body(model="text-embedding-3-small", input="x")).message
    assert MODEL in message
    assert "'text-embedding-3-small'" in message
    assert "EMBEDDING_MODEL" in message and "EMBEDDING_BASE_URL" in message


def test_the_answer_has_the_shape_of_the_openai_embeddings_api() -> None:
    answer = server.answer_for(["alpha beta", "gamma"], fake_embed)
    assert answer["object"] == "list"
    assert answer["model"] == MODEL
    assert [row["index"] for row in answer["data"]] == [0, 1]
    assert all(row["object"] == "embedding" for row in answer["data"])
    assert answer["data"][0]["embedding"] == [10.0, 1.0]
    assert answer["usage"] == {"prompt_tokens": 3, "total_tokens": 3}


@pytest.fixture
def running() -> Iterator[str]:
    def embed(texts: Sequence[str]) -> tuple[list[list[float]], int]:
        if any("explode" in text for text in texts):
            raise RuntimeError("the sentinel inside an error")
        return fake_embed(texts)

    service = server.serve(embed, 0, "127.0.0.1")
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{service.server_address[1]}"
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)


def send(address: str, path: str, data: bytes | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(
        address + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_over_http_it_answers_health_and_embeddings_and_refuses_the_rest_in_words(
    running: str,
) -> None:
    assert send(running, "/health") == (200, {"status": "ok", "model": MODEL})
    status, answer = send(running, "/v1/embeddings", body(input=["one", "three"]))
    assert status == 200
    assert [row["embedding"][0] for row in answer["data"]] == [3.0, 5.0]
    status, answer = send(running, "/v1/embeddings", body(input=[]))
    assert status == 400 and "nothing to embed" in answer["error"]["message"]
    status, answer = send(running, "/v1/models")
    assert status == 404 and "POST /v1/embeddings" in answer["error"]["message"]
    status, answer = send(running, "/v1/chat/completions", b"{}")
    assert status == 404 and "no such address" in answer["error"]["message"]
    status, answer = send(running, "/v1/embeddings")
    assert status == 405 and "POST" in answer["error"]["message"]


def test_a_failure_inside_is_not_told_and_no_text_is_ever_logged(
    running: str, capsys: pytest.CaptureFixture[str]
) -> None:
    status, answer = send(running, "/v1/embeddings", body(input="the text nobody may see"))
    assert status == 200
    status, answer = send(running, "/v1/embeddings", body(input=["explode with a private text"]))
    assert status == 500
    assert "could not embed" in answer["error"]["message"]
    assert "sentinel" not in json.dumps(answer) and "private text" not in json.dumps(answer)
    seen = capsys.readouterr()
    everything = seen.out + seen.err
    assert "nobody may see" not in everything and "private text" not in everything
    assert "sentinel" not in everything
    # What it does log is what was asked for and how long it took: one line a request.
    lines = [json.loads(line) for line in seen.out.splitlines() if line.startswith("{")]
    assert {k: lines[0][k] for k in ("path", "texts")} == {"path": "/v1/embeddings", "texts": 1}


def test_compose_and_the_service_agree_on_the_model_the_address_and_the_port() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    model = re.search(r"EMBEDDING_MODEL: \$\{EMBEDDING_MODEL-([^}]*)\}", compose)
    address = re.search(r"EMBEDDING_BASE_URL: \$\{EMBEDDING_BASE_URL-([^}]*)\}", compose)
    assert model is not None and address is not None
    assert model.group(1) == MODEL
    port = re.search(r'"EMBEDDER_PORT", "(\d+)"', (EMBEDDER / "server.py").read_text("utf-8"))
    assert port is not None
    assert address.group(1) == f"http://embeddings:{port.group(1)}/v1"
    block = compose[compose.index("\n  embeddings:") :]
    block = block[: block.index("\nvolumes:")]
    assert "build: ./embedder" in block
    assert f"127.0.0.1:{port.group(1)}/health" in block
    # It is only for Vrum: no port is published, and the app does not wait for it.
    assert "ports:" not in block
    app = compose[compose.index("\n  app:") : compose.index("\n  embeddings:")]
    assert "embeddings" not in app.split("environment:")[0]
    # A value set to nothing must mean nothing, so the defaults use `-` and not `:-`.
    assert ":-http://embeddings" not in compose and f":-{MODEL}" not in compose


def test_the_check_stack_leaves_the_real_service_out_and_keeps_its_stand_in() -> None:
    check = (ROOT / "compose.check.yaml").read_text(encoding="utf-8")
    assert re.search(r'\n  embeddings:\n    profiles: \["real-embeddings"\]', check)
    assert "EMBEDDING_BASE_URL: http://scripted-embeddings:8080/v1" in check


def test_the_image_is_made_only_from_pinned_and_checksummed_files() -> None:
    requirements = (EMBEDDER / "requirements.txt").read_text(encoding="utf-8")
    pinned = re.findall(r"^([a-z0-9._-]+)==\S+", requirements, re.MULTILINE | re.IGNORECASE)
    hashes = re.findall(r"--hash=sha256:[0-9a-f]{64}", requirements)
    assert {"onnxruntime", "tokenizers", "numpy"} <= {name.lower() for name in pinned}
    assert len(hashes) >= len(pinned)
    assert "--require-hashes" in (EMBEDDER / "Dockerfile").read_text(encoding="utf-8")
    fetch = (EMBEDDER / "fetch_model.py").read_text(encoding="utf-8")
    assert re.search(r'REVISION = "[0-9a-f]{40}"', fetch)
    assert len(re.findall(r'"[0-9a-f]{64}"', fetch)) == 2
    # Nothing is fetched when it runs: the server never opens a connection to anything.
    assert "urllib.request" not in (EMBEDDER / "server.py").read_text(encoding="utf-8")
