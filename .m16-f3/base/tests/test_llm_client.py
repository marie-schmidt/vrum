"""The language model client against a real HTTP server on this machine, so what it does with each
kind of answer (and with no answer at all) is seen as it really happens."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from vrum.config import LanguageModelSettings
from vrum.llm import LanguageModel, ModelAnswerUnusable, ModelUnavailable, parse_answer

pytestmark = pytest.mark.anyio


class _Service:
    """What the fake service does next, and what it was sent."""

    def __init__(self) -> None:
        self.status = 200
        self.content: Any = '{"facts": []}'
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
                    {"choices": [{"index": 0, "message": {"content": state.content}}]}
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


def model_for(port: int, api_key: str | None = None, timeout: float = 5.0) -> LanguageModel:
    return LanguageModel(
        LanguageModelSettings(
            base_url=f"http://127.0.0.1:{port}/v1",
            model="a-model",
            api_key=api_key,
            timeout_seconds=timeout,
            max_input_chars=30000,
        )
    )


async def test_it_sends_the_model_and_both_messages_and_gives_back_the_json_object(
    service: _Service,
) -> None:
    service.content = '{"facts": [{"text": "x"}]}'

    answer = await model_for(service.port).ask("Be brief.", "What is in here?")

    assert answer == {"facts": [{"text": "x"}]}
    ((headers, request),) = service.sent
    assert request["model"] == "a-model" and request["temperature"] == 0
    assert request["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "What is in here?"},
    ]
    assert request["response_format"] == {"type": "json_object"}
    assert "Authorization" not in headers


async def test_it_sends_the_key_when_there_is_one(service: _Service) -> None:
    await model_for(service.port, api_key="the-key").ask("s", "u")
    assert service.sent[0][0]["Authorization"] == "Bearer the-key"


@pytest.mark.parametrize(
    "content",
    ['```json\n{"a": 1}\n```', '```\n{"a": 1}\n```', '  {"a": 1}  \n'],
)
def test_a_code_fence_or_spaces_around_the_json_are_forgiven(content: str) -> None:
    assert parse_answer(content) == {"a": 1}


@pytest.mark.parametrize("content", ["Sure! Here are the facts.", "[1, 2]", '"text"', ""])
def test_anything_else_is_not_a_json_object_and_is_said_so(content: str) -> None:
    with pytest.raises(ModelAnswerUnusable, match="nothing was changed"):
        parse_answer(content)


async def test_a_reply_that_is_not_json_is_unusable_and_not_a_crash(service: _Service) -> None:
    service.content = "I cannot help with that."
    with pytest.raises(ModelAnswerUnusable, match="did not answer with JSON"):
        await model_for(service.port).ask("s", "u")


async def test_a_service_that_answers_an_error_is_unavailable_and_says_which(
    service: _Service,
) -> None:
    service.status = 503
    service.body = b"overloaded"
    with pytest.raises(ModelUnavailable, match=r"answered 503: overloaded"):
        await model_for(service.port).ask("s", "u")


async def test_a_service_that_does_not_speak_the_chat_protocol_is_unavailable(
    service: _Service,
) -> None:
    for body in (b"nope", b'{"data": []}', b'{"choices": []}'):
        service.body = body
        with pytest.raises(ModelUnavailable, match="OpenAI-compatible chat service"):
            await model_for(service.port).ask("s", "u")
    service.body = None
    service.content = None
    with pytest.raises(ModelUnavailable, match="answered with no text"):
        await model_for(service.port).ask("s", "u")


async def test_no_service_at_all_is_unavailable_and_says_where_it_looked() -> None:
    with pytest.raises(ModelUnavailable, match=r"127\.0\.0\.1:9/v1 cannot be reached"):
        await model_for(9, timeout=2.0).ask("s", "u")
