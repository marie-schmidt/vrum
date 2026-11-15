"""The language model client: the one small interface to an OpenAI-compatible chat service.

It sends one question and returns the JSON object the model answered with. It knows nothing about
the graph, and it does not decide whether an answer can be believed: that is the caller's job."""

import asyncio
import json
import re
import urllib.error
import urllib.request
from typing import Any

from vrum.config import LanguageModelSettings


class ModelFailure(Exception):
    """The model gave nothing usable. The message says why, in plain words."""


class ModelUnavailable(ModelFailure):
    """The service cannot be reached, or failed. Trying again later may work."""


class ModelAnswerUnusable(ModelFailure):
    """The service answered, but not with a JSON object that could be read."""


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_answer(content: str) -> dict[str, Any]:
    """The JSON object in a model's reply. Some models wrap it in a code fence; nothing else is
    forgiven."""
    text = content.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except ValueError as error:
        raise ModelAnswerUnusable(
            "The language model did not answer with JSON, so its answer cannot be used and "
            "nothing was changed. Try again, or try a model that follows instructions better."
        ) from error
    if not isinstance(value, dict):
        raise ModelAnswerUnusable(
            "The language model answered with JSON that is not an object, so its answer cannot "
            "be used and nothing was changed."
        )
    return value


class LanguageModel:
    def __init__(self, settings: LanguageModelSettings) -> None:
        self._settings = settings

    @property
    def model(self) -> str:
        return self._settings.model

    @property
    def max_input_chars(self) -> int:
        return self._settings.max_input_chars

    async def ask(self, system: str, user: str) -> dict[str, Any]:
        """The JSON object the model answers with. Raises ModelUnavailable or
        ModelAnswerUnusable."""
        return parse_answer(await asyncio.to_thread(self._ask_now, system, user))

    def _ask_now(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self._settings.api_key:
            headers["Authorization"] = f"Bearer {self._settings.api_key}"
        body = {
            "model": self._settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self._settings.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._settings.timeout_seconds) as reply:
                payload = reply.read()
        except urllib.error.HTTPError as error:
            raise ModelUnavailable(
                f"The language model at {self._settings.base_url} answered {error.code}"
                f"{_detail(error)}. Check LLM_BASE_URL, LLM_MODEL and LLM_API_KEY."
            ) from error
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ModelUnavailable(
                f"The language model at {self._settings.base_url} cannot be reached ({reason})."
            ) from error
        try:
            content = json.loads(payload)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ModelUnavailable(
                f"The language model at {self._settings.base_url} did not answer the way an "
                "OpenAI-compatible chat service does (a list called choices, each with a "
                "message)."
            ) from error
        if not isinstance(content, str):
            raise ModelUnavailable(
                f"The language model at {self._settings.base_url} answered with no text."
            )
        return content


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        text = error.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""
    return f": {text[:200]}" if text else ""
