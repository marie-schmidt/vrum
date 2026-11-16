"""A small OpenAI-compatible chat service for the check stack (compose.check.yaml).

It lets ./check.sh prove what Vrum does with a language model's output (keeping what checks out,
dropping what does not, never sending a secret) without a model and with answers that are the same
every run. It does not prove how good a real model is: a real model is tried by hand, with
scripts/real_model_check.py (see AGENTS.md).

A test tells it what to answer, and later asks what it was sent:
  POST /script            {"when": text, "reply": JSON or text, "status": optional HTTP status}
                          From now on a request whose messages contain `when` is answered with
                          `reply` (JSON is sent as the model's text). Newest rule first.
  GET  /received          every request it has had: {"n", "model", "system", "user", "text"}
  POST /forget            forget every rule and every request
  POST /v1/chat/completions   what Vrum calls. With no matching rule it answers {}
  GET  /health
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL = "scripted-model"

_lock = threading.Lock()
_rules: list[dict[str, Any]] = []
_received: list[dict[str, Any]] = []


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: object) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> Any:
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        elif self.path == "/received":
            with _lock:
                self._send(200, list(_received))
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        try:
            request = self._body()
        except ValueError:
            self._send(400, {"error": {"message": "the body must be JSON"}})
            return
        if self.path == "/script":
            with _lock:
                _rules.insert(0, request)
            self._send(200, {"rules": len(_rules)})
        elif self.path == "/forget":
            with _lock:
                _rules.clear()
                _received.clear()
            self._send(200, {"rules": 0})
        elif self.path == "/v1/chat/completions":
            self._complete(request)
        else:
            self._send(404, {"error": {"message": "not found"}})

    def _complete(self, request: dict[str, Any]) -> None:
        messages = request.get("messages", [])
        system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        user = "\n".join(m["content"] for m in messages if m.get("role") == "user")
        text = "\n".join(m["content"] for m in messages)
        with _lock:
            _received.append(
                {
                    "n": len(_received) + 1,
                    "model": request.get("model"),
                    "system": system,
                    "user": user,
                    "text": text,
                }
            )
            rule = next((rule for rule in _rules if rule["when"] in text), None)
        if rule is not None and rule.get("status"):
            self._send(int(rule["status"]), {"error": {"message": "scripted failure"}})
            return
        reply = rule["reply"] if rule is not None else {}
        content = reply if isinstance(reply, str) else json.dumps(reply)
        self._send(
            200,
            {
                "id": "scripted",
                "object": "chat.completion",
                "model": request.get("model", MODEL),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), _Handler).serve_forever()
