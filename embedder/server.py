"""The embedding service Vrum runs by default.

It turns text into lists of numbers so that texts can be compared by what they mean, and nothing
else: no language model, no generated text. It serves one small open-source model, all-MiniLM-L6-v2
(Apache 2.0, 384 numbers per text), from files baked into the image when it was built, so it needs
no internet and downloads nothing when it starts. It speaks the one call of the OpenAI embeddings
API that Vrum needs:

    POST /v1/embeddings   {"model": "all-MiniLM-L6-v2", "input": "a text" or ["a text", ...]}
    GET  /health          200 once the model is loaded

It is not published outside Docker: only the other services of the compose project reach it, so it
asks for no key. Every refusal is a plain sentence, and no text that was sent is ever logged.

The model files are loaded only when the service starts (load_model), so the request handling
below can be tried without them (tests/test_embedder.py)."""

import json
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MODEL_NAME = "all-MiniLM-L6-v2"
DIMENSIONS = 384
# The model was trained on texts of up to 256 tokens (about a thousand characters); a longer text
# is cut there, as the model's own library does.
MAX_TOKENS = 256
# What one request may hold, so that a mistake cannot tie the service up.
MAX_TEXTS = 128
MAX_BODY_BYTES = 4 * 1024 * 1024
# How many texts the model reads at once.
CHUNK = 16

NO_SUCH_ADDRESS = (
    "There is no such address. This service answers POST /v1/embeddings and GET /health."
)
TOO_BIG = "The request is larger than 4 MiB. Send fewer or shorter texts."

# Texts in, one vector for each out, and how many tokens the texts held altogether.
Embed = Callable[[Sequence[str]], tuple[list[list[float]], int]]


class Refused(Exception):
    """A request the service will not answer, with the status to say it with and why, in words."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def texts_in(body: bytes) -> list[str]:
    """The texts a request asks for, or Refused saying what is wrong with it."""
    if len(body) > MAX_BODY_BYTES:
        raise Refused(413, TOO_BIG)
    try:
        request = json.loads(body)
    except ValueError:
        raise Refused(400, "The request body is not valid JSON.") from None
    if not isinstance(request, dict):
        raise Refused(400, "The request body must be a JSON object with a model and an input.")
    model = request.get("model")
    if model != MODEL_NAME:
        wanted = f"'{model}'" if isinstance(model, str) else "none"
        raise Refused(
            400,
            f"This service has one model, {MODEL_NAME}, and the request asked for {wanted}. Set "
            f"EMBEDDING_MODEL to {MODEL_NAME}, or point EMBEDDING_BASE_URL at another service.",
        )
    if request.get("encoding_format", "float") != "float":
        raise Refused(
            400, "Only float vectors are served: leave encoding_format out or set it to float."
        )
    if request.get("dimensions", DIMENSIONS) != DIMENSIONS:
        raise Refused(
            400, f"This model's vectors are {DIMENSIONS} numbers long and cannot be shortened."
        )
    if "input" not in request:
        raise Refused(400, "The request needs an input: a text, or a list of texts.")
    given = request["input"]
    texts = [given] if isinstance(given, str) else given
    if not isinstance(texts, list) or not all(isinstance(text, str) for text in texts):
        raise Refused(400, "The input must be a text, or a list of texts (nothing else).")
    if not texts:
        raise Refused(400, "The input is an empty list, so there is nothing to embed.")
    if len(texts) > MAX_TEXTS:
        raise Refused(
            400,
            f"At most {MAX_TEXTS} texts can be sent at once, and this request has {len(texts)}.",
        )
    return texts


def answer_for(texts: Sequence[str], embed: Embed) -> dict[str, Any]:
    """The reply the OpenAI embeddings API gives for these texts."""
    vectors, tokens = embed(texts)
    return {
        "object": "list",
        "model": MODEL_NAME,
        "data": [
            {"object": "embedding", "index": number, "embedding": vector}
            for number, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


def make_handler(embed: Embed) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "vrum-embedder"

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _refuse(self, refusal: Refused) -> None:
            self._send(refusal.status, {"error": {"message": refusal.message}})

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, {"status": "ok", "model": MODEL_NAME})
            elif self.path == "/v1/embeddings":
                self._refuse(Refused(405, "Send the texts with POST, not GET."))
            else:
                self._refuse(Refused(404, NO_SUCH_ADDRESS))

        def do_POST(self) -> None:
            started = time.monotonic()
            count = 0
            try:
                if self.path != "/v1/embeddings":
                    raise Refused(404, NO_SUCH_ADDRESS)
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY_BYTES:
                    raise Refused(413, TOO_BIG)
                texts = texts_in(self.rfile.read(length))
                count = len(texts)
                self._send(200, answer_for(texts, embed))
            except Refused as refusal:
                self._refuse(refusal)
            except Exception:
                # Whatever it was, it is in this service's own log and not in the answer.
                print(
                    json.dumps({"event": "failed", "path": self.path}), file=sys.stderr, flush=True
                )
                self._refuse(
                    Refused(
                        500,
                        "The service could not embed these texts. Try again, or look at its log.",
                    )
                )
            # What was asked, how many texts and how long it took: never the texts themselves.
            line = {
                "path": self.path,
                "texts": count,
                "seconds": round(time.monotonic() - started, 3),
            }
            print(json.dumps(line), flush=True)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def load_model(directory: Path) -> Embed:
    """Load the model and its tokenizer from `directory` and give back the function that embeds."""
    import numpy
    import onnxruntime
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=MAX_TOKENS)
    tokenizer.enable_padding()
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = min(4, os.cpu_count() or 1)
    # Texts differ in length, so memory that is kept for the longest one would only sit there.
    options.enable_cpu_mem_arena = False
    session = onnxruntime.InferenceSession(
        str(directory / "model.onnx"), options, providers=["CPUExecutionProvider"]
    )
    wanted = {entry.name for entry in session.get_inputs()}
    # The session serves one request at a time: a few at once would only fight for the cores.
    one_at_a_time = threading.Lock()

    def embed_few(texts: Sequence[str]) -> tuple[list[list[float]], int]:
        encodings = tokenizer.encode_batch(list(texts))
        ids = numpy.array([one.ids for one in encodings], dtype=numpy.int64)
        mask = numpy.array([one.attention_mask for one in encodings], dtype=numpy.int64)
        inputs = {
            "input_ids": ids,
            "attention_mask": mask,
            "token_type_ids": numpy.zeros_like(ids),
        }
        with one_at_a_time:
            hidden = session.run(None, {k: v for k, v in inputs.items() if k in wanted})[0]
        # The mean of the word vectors, leaving out the padding, made one unit long: the way the
        # model was trained, so that the dot product of two vectors is how alike the texts are.
        weights = mask[:, :, None].astype(numpy.float32)
        pooled = (hidden * weights).sum(axis=1) / numpy.maximum(weights.sum(axis=1), 1e-9)
        pooled /= numpy.maximum(numpy.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
        return pooled.astype(numpy.float32).tolist(), int(mask.sum())

    def embed(texts: Sequence[str]) -> tuple[list[list[float]], int]:
        # A few texts at a time, so that the memory one request needs does not grow with how many
        # it holds (what the model keeps while it reads grows with the square of a text's length).
        vectors: list[list[float]] = []
        tokens = 0
        for start in range(0, len(texts), CHUNK):
            more, counted = embed_few(texts[start : start + CHUNK])
            vectors.extend(more)
            tokens += counted
        return vectors, tokens

    return embed


def serve(embed: Embed, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer:  # noqa: S104
    """Listening, with the model already loaded: the service is ready as soon as it listens."""
    server = ThreadingHTTPServer((host, port), make_handler(embed))
    server.daemon_threads = True
    return server


if __name__ == "__main__":
    folder = Path(os.environ.get("EMBEDDER_MODEL_DIR", "/model"))
    port = int(os.environ.get("EMBEDDER_PORT", "8080"))
    running = serve(load_model(folder), port)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=running.shutdown).start())
    print(json.dumps({"event": "ready", "model": MODEL_NAME, "port": port}), flush=True)
    running.serve_forever()
