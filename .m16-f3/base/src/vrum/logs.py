"""Structured logs: one JSON object per line, on standard error, with no secret in any of them.

A line says what happened (`event`) and a few plain facts about it (which tool, who asked, how
long, what came of it). It never holds a request body, a header, a token, a password or a key:
nothing that holds one is ever passed to the logger, and as a second guard every line is scrubbed
of the secret values this server holds and of anything in a well-known secret format before it is
written."""

import json
import logging
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from vrum.secret_formats import redact_secrets

# A secret shorter than this is left alone by the scrub: replacing a two-letter password wherever
# it appears would make every line unreadable.
_SHORTEST_SCRUBBED = 8


class Scrubber:
    """Replaces the secret values this server holds, and anything in a well-known secret
    format, in a text."""

    def __init__(self, secrets: Iterable[str]) -> None:
        self._secrets = sorted(
            {secret for secret in secrets if len(secret) >= _SHORTEST_SCRUBBED},
            key=len,
            reverse=True,
        )

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "[secret removed]")
        return redact_secrets(text)

    def deep(self, value: Any) -> Any:
        if isinstance(value, str):
            return self(value)
        if isinstance(value, dict):
            return {str(key): self.deep(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.deep(item) for item in value]
        return value


class JsonFormatter(logging.Formatter):
    def __init__(self, scrubber: Scrubber) -> None:
        super().__init__()
        self._scrub = scrubber

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            line.update(fields)
        else:
            line["logger"] = record.name
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        # Scrub the parts, then write: a secret with a quote in it would not match once the line
        # had been written as JSON.
        return json.dumps(self._scrub.deep(line), ensure_ascii=False)


def configure_logging(secrets: Iterable[str]) -> None:
    """Send every log line, ours and the libraries', through the JSON format and the scrub."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(Scrubber(secrets)))
    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        library = logging.getLogger(name)
        library.handlers.clear()
        library.propagate = True
    # The server writes a line of its own for every tool call, so the library's would repeat it.
    logging.getLogger("mcp.server.mcpserver.server").setLevel(logging.WARNING)


def event(logger: logging.Logger, level: int, name: str, **fields: Any) -> None:
    """Write one structured line: the name of what happened and plain facts about it."""
    logger.log(level, name, extra={"fields": fields})
