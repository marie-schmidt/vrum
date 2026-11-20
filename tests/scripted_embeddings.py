"""A small OpenAI-compatible embedding service for the check stack (compose.check.yaml).

It lets ./check.sh prove what Vrum does with an embedding service (finding things, saying how they
matched, surviving the service being stopped, rebuilding the index) without building the real one
and with answers that are the same every run. It does not prove how good a real model is: the
real model (the `embeddings` service of compose.yaml) is measured by hand with
scripts/real_embeddings_check.py, and tests/test_embeddings_second_stack.py proves it works
out of the box.

The "meaning" of a text here is the set of concepts its words stand for. Words in one group below
stand for the same concept, so two texts that use different words for the same ideas get the same
vector, and texts about different ideas get vectors that are almost at right angles. Any other word
stands for itself.

Run as `python scripted_embeddings.py`. It listens on port 8080 and answers:
  POST /v1/embeddings    {"model": ..., "input": text or [text, ...]}
  GET  /health
Input containing REJECT_MARKER is refused with a 400, as a real service refuses text it cannot
take, so that tests can see what Vrum does with one refused item.
"""

import hashlib
import json
import math
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIMENSIONS = 1024
MODEL = "scripted-embedding"
REJECT_MARKER = "[[reject-me]]"

_GROUPS = (
    "invoice bill billing payment charge charged pay",
    "customer client user buyer shopper",
    "email mail message notify notification alert",
    "login signin sign authenticate authentication credential password",
    "export download extract dump",
    "import upload ingest load",
    "report summary overview dashboard digest",
    "search find lookup locate query",
    "slow sluggish latency delay lag",
    "fast quick speedy rapid",
    "delete remove erase purge",
    "create add new make build",
    "refund reimburse repay",
    "cancel terminate end stop",
    "subscription plan membership tier",
    "mobile phone smartphone android ios",
    "dark night theme",
    "backup snapshot archive",
    "restore recover rollback",
    "schedule calendar timetable",
    "translate language locale localisation",
    "permission access role privilege",
    "audit log history trail",
    "image photo picture avatar",
)
_CONCEPT = {word: group[0] for group in map(str.split, _GROUPS) for word in group}
_STOP_TEXT = (
    "the a an of to for and or in on by with is are be we it that this as at from so can "
    "should will must when if then than into over per each every all any"
)
_STOPWORDS = frozenset(_STOP_TEXT.split())


def concepts(text: str) -> list[str]:
    found = []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in _STOPWORDS:
            continue
        if len(word) > 3 and word.endswith("s") and word not in _CONCEPT:
            word = word[:-1]
        found.append(_CONCEPT.get(word, word))
    return found


def embed(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for concept in concepts(text):
        digest = hashlib.sha256(concept.encode()).digest()
        for part in range(3):
            slot = int.from_bytes(digest[part * 2 : part * 2 + 2], "big") % DIMENSIONS
            vector[slot] += 1.0 if digest[8 + part] % 2 else -1.0
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length else vector


def cosine(first: list[float], second: list[float]) -> float:
    return sum(a * b for a, b in zip(first, second, strict=True))


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: object) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        if self.path != "/v1/embeddings":
            self._send(404, {"error": {"message": "not found"}})
            return
        try:
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            texts = request["input"]
            texts = [texts] if isinstance(texts, str) else list(texts)
        except (ValueError, KeyError, TypeError):
            self._send(400, {"error": {"message": "input must be a text or a list of texts"}})
            return
        if any(REJECT_MARKER in text for text in texts):
            self._send(400, {"error": {"message": "this input is too long for the model"}})
            return
        self._send(
            200,
            {
                "object": "list",
                "model": request.get("model", MODEL),
                "data": [
                    {"object": "embedding", "index": index, "embedding": embed(text)}
                    for index, text in enumerate(texts)
                ],
            },
        )

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), _Handler).serve_forever()
