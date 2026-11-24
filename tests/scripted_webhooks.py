"""A stand-in for the place a webhook goes (the check stack's `scripted-webhooks`, M48): a small
HTTP service that keeps every message it is sent and answers the way its address says, so that the
checks can see what Vrum sends, when, how often, and what it does when the receiver misbehaves,
with no real service and answers that are the same every run.

    POST /hook/<mode>[?anything]    one message: it is kept, then answered as `mode` says:
        ok         200 and a short JSON body
        fail       500
        refuse     401
        lie        200 with a body that says it failed (a 2xx is a delivery whatever the body says)
        slow       200 after 20 seconds (longer than Vrum waits)
        redirect   302 to /hook/after-redirect, which a sender that followed it would visit
        after-redirect   200 (it is only here to be seen in the log)
    GET  /_log       every message kept, oldest first: path, headers (lower case names), body
    POST /_reset     forget them all
    GET  /health

It does not check a signature: the tests do, with the secret they were given, using
vrum.webhook_signing, so the sender and the checker are one piece of code. Run as
`python scripted_webhooks.py`; it listens on port 8030."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8030
SLOW_SECONDS = 20
_LOCK = threading.Lock()
_LOG: list[dict[str, object]] = []


class _Handler(BaseHTTPRequestHandler):
    def _send(
        self, status: int, body: object = None, headers: dict[str, str] | None = None
    ) -> None:
        payload = json.dumps(body if body is not None else {"ok": True}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200)
        elif self.path == "/_log":
            with _LOCK:
                self._send(200, list(_LOG))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", "replace")
        if self.path == "/_reset":
            with _LOCK:
                _LOG.clear()
            self._send(200)
            return
        if not self.path.startswith("/hook/"):
            self._send(404, {"error": "not found"})
            return
        mode = self.path[len("/hook/") :].split("?", 1)[0]
        with _LOCK:
            _LOG.append(
                {
                    "path": self.path,
                    "mode": mode,
                    "at": time.time(),
                    "headers": {name.lower(): value for name, value in self.headers.items()},
                    "body": body,
                }
            )
        if mode == "fail":
            self._send(500, {"error": "broken"})
        elif mode == "refuse":
            self._send(401, {"error": "who are you"})
        elif mode == "lie":
            self._send(200, {"ok": False, "error": "this did not work"})
        elif mode == "slow":
            time.sleep(SLOW_SECONDS)
            self._send(200)
        elif mode == "redirect":
            self._send(302, {"moved": True}, {"Location": "/hook/after-redirect"})
        else:
            self._send(200)

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), _Handler).serve_forever()
