"""M29: search by meaning works with nothing set up. Each test starts a second, separate copy of
Vrum (tests/second_stack.py) built from compose.yaml alone, with no .env and nothing set, the way a
person's first `./start.sh` runs it, and this time with the embedding service that comes with it:
a small model in its own container (embedder/), reached by the app as embeddings:8080.

It proves what ./check.sh cannot with its scripted stand-in: the real model answers with 384
numbers and puts reworded texts near each other, a note is found by a word it does not contain, the
status page and the board's index route say what is true, the app starts and answers while the
service is down (and says plainly that only exact words were used), everything catches up when it
returns, and a setting of your own replaces the built-in service."""

import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import anyio
import pytest
from mcp import Client

from tests.helpers import ok, refused
from tests.second_stack import SecondStack

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MODEL = "all-MiniLM-L6-v2"
STORY = "Customers receive a receipt PDF by email once their payment has gone through"
# Nothing in common with STORY in words: no word of one is a word of the other, even after stemming.
PARAPHRASE = "an invoice mailed to a client after charging them"

ASK = """
import json, sys, urllib.request, urllib.error
request = urllib.request.Request(
    'http://embeddings:8080/v1/embeddings',
    data=sys.argv[1].encode(),
    headers={'Content-Type': 'application/json'},
    method='POST',
)
try:
    with urllib.request.urlopen(request, timeout=30) as reply:
        print(reply.status, reply.read().decode())
except urllib.error.HTTPError as error:
    print(error.code, error.read().decode())
"""


@pytest.fixture
def stack() -> Iterator[SecondStack]:
    second = SecondStack(nothing_set=True, embeddings=True)
    try:
        yield second
    finally:
        second.close()


def ask_the_service(stack: SecondStack, request: dict[str, Any]) -> tuple[int, Any]:
    """What the embedding service answers the app, asked from inside the app's own container: the
    service has no published port, so this is the only way in."""
    result = stack.compose("exec", "-T", "app", "python", "-c", ASK, json.dumps(request))
    assert result.returncode == 0, result.stderr
    status, _, answer = result.stdout.strip().partition(" ")
    return int(status), json.loads(answer)


def cosine(one: list[float], other: list[float]) -> float:
    return sum(a * b for a, b in zip(one, other, strict=True))


async def made(client: Client, kind: str, title: str, parent: str | None = None) -> str:
    arguments: dict[str, Any] = {"kind": kind, "title": title}
    if parent:
        arguments["parent"] = parent
    return str((await ok(client, "create_node", **arguments))["ref"])


async def index_of(client: Client) -> dict[str, Any]:
    status: dict[str, Any] = (await ok(client, "server_status"))["index"]
    return status


async def until(client: Client, wanted: str, seconds: float = 90) -> dict[str, Any]:
    """Wait until the index is as described: `caught_up` (answering, nothing waiting, used)."""
    deadline = time.monotonic() + seconds
    while True:
        index = await index_of(client)
        if wanted == "caught_up" and (
            index["embedding_service"] == "answering"
            and index["waiting_to_index"] == 0
            and index["meaning"] == "used"
        ):
            return index
        if wanted == "unreachable" and index["meaning"] == "service_unreachable":
            return index
        assert time.monotonic() < deadline, f"waited for {wanted}, and the index is {index}"
        await anyio.sleep(1)


def board_index(stack: SecondStack) -> dict[str, Any]:
    """What the board's top bar reads: the index route, as the signed-in owner."""
    assert stack.project is not None
    reply = stack.project.owner.call("GET", f"/board/api/projects/{stack.project.id}/index")
    assert reply.status == 200, reply.body
    assert isinstance(reply.body, dict)
    return reply.body


def health(stack: SecondStack, seconds: float = 90) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            with urllib.request.urlopen(f"{stack.base}/health", timeout=3) as reply:
                if reply.status == 200:
                    return
        except OSError:
            pass
        assert time.monotonic() < deadline, "the app did not answer"
        time.sleep(1)


async def test_with_nothing_set_a_note_is_found_by_a_word_it_does_not_contain(
    stack: SecondStack,
) -> None:
    config = stack.compose("config")
    assert config.returncode == 0, config.stderr
    assert "EMBEDDING_BASE_URL: http://embeddings:8080/v1" in config.stdout
    assert f"EMBEDDING_MODEL: {MODEL}" in config.stdout
    stack.up()

    # The service itself: 384 numbers of unit length for each text, reworded texts near each other
    # and unrelated ones far apart, and a request it cannot answer refused in plain words.
    status, answer = ask_the_service(
        stack, {"model": MODEL, "input": [STORY, PARAPHRASE, "The dark theme can be switched on"]}
    )
    assert status == 200
    vectors = [row["embedding"] for row in answer["data"]]
    assert [len(vector) for vector in vectors] == [384, 384, 384]
    assert all(abs(math.sqrt(cosine(vector, vector)) - 1) < 0.001 for vector in vectors)
    assert cosine(vectors[0], vectors[1]) > 0.45
    assert cosine(vectors[0], vectors[2]) < 0.2
    status, answer = ask_the_service(stack, {"model": "gpt-4-embeddings", "input": "x"})
    assert status == 400 and MODEL in answer["error"]["message"]
    status, answer = ask_the_service(stack, {"model": MODEL, "input": []})
    assert status == 400 and "nothing to embed" in answer["error"]["message"]

    async with stack.client() as client:
        epic = await made(client, "epic", "Payments")
        story = await made(client, "story", STORY, epic)
        await made(client, "story", "Dark mode for the interface", epic)
        index = await until(client, "caught_up")
        # What the status page and the board's top bar say.
        assert index["embedding_model"] == MODEL and index["vector_size"] == 384
        assert index["indexed"] >= 3
        assert board_index(stack)["meaning"] == "used"
        assert board_index(stack)["waiting_to_index"] == 0

        by_words = await ok(client, "search", query=PARAPHRASE, mode="words")
        assert [item["ref"] for item in by_words["items"]] == []
        found = await ok(client, "search", query=PARAPHRASE, mode="meaning")
        assert found["index"]["meaning"] == "used"
        assert found["items"][0]["ref"] == story, found["items"]
        assert found["items"][0]["matched"] == "meaning"


async def test_the_app_starts_and_answers_without_the_service_and_everything_catches_up(
    stack: SecondStack,
) -> None:
    stack.up()
    async with stack.client() as client:
        epic = await made(client, "epic", "Payments")
        story = await made(client, "story", STORY, epic)
        await until(client, "caught_up")

        assert stack.compose("stop", "embeddings").returncode == 0
        index = await index_of(client)
        assert index["embedding_service"] == "unreachable"
        # A search says plainly that only exact words were used, and exact words still work.
        both = await ok(client, "search", query="receipt", mode="both")
        assert both["index"]["meaning"] == "service_unreachable"
        assert "Only exact words were searched" in both["index"]["note"]
        assert story in [item["ref"] for item in both["items"]]
        assert "meaning" in (await refused(client, "search", query=PARAPHRASE, mode="meaning"))

        # An item written now waits, and the board's index route (what the top bar shows) says that
        # the service is not answering and how many wait.
        late = await made(client, "story", "Written while the service is away", epic)
        index = await until(client, "unreachable")
        assert index["waiting_to_index"] >= 1
        shown = board_index(stack)
        assert shown["meaning"] == "service_unreachable" and shown["waiting_to_index"] >= 1

    # The app starts again with the service still down, and answers (it does not wait for it).
    assert stack.compose("restart", "app").returncode == 0
    health(stack)
    async with stack.client() as client:
        words = await ok(client, "search", query="away", mode="words")
        assert late in [item["ref"] for item in words["items"]]

        # When the service is back the index catches up by itself, and the item written while it
        # was away is found by meaning too.
        assert stack.compose("start", "embeddings").returncode == 0
        index = await until(client, "caught_up")
        assert board_index(stack)["meaning"] == "used"
        found = await ok(
            client, "search", query="written when the service was not there", mode="meaning"
        )
        assert late in [item["ref"] for item in found["items"]]
        assert index["indexed"] >= 3


async def test_a_setting_of_your_own_replaces_the_built_in_service_and_nothing_means_none(
    stack: SecondStack,
) -> None:
    stack.up()
    async with stack.client() as client:
        epic = await made(client, "epic", "Payments")
        await made(client, "story", STORY, epic)
        assert (await until(client, "caught_up"))["embedding_model"] == MODEL

    # Another model name: the app asks for that one, and says so.
    stack.env["EMBEDDING_MODEL"] = "a-model-of-my-own"
    result = stack.compose("up", "--detach", "--no-deps", "--wait", "--wait-timeout", "120", "app")
    assert result.returncode == 0, result.stdout + result.stderr
    health(stack)
    async with stack.client() as client:
        index = await index_of(client)
        assert index["embedding_model"] == "a-model-of-my-own"

    # Both set to nothing: no meaning search at all, and it says so, in the status and to a search.
    stack.env["EMBEDDING_BASE_URL"] = ""
    stack.env["EMBEDDING_MODEL"] = ""
    result = stack.compose("up", "--detach", "--no-deps", "--wait", "--wait-timeout", "120", "app")
    assert result.returncode == 0, result.stdout + result.stderr
    health(stack)
    async with stack.client() as client:
        index = await index_of(client)
        assert index["meaning"] == "not_configured"
        assert index["embedding_service"] == "not_configured" and index["embedding_model"] is None
        assert board_index(stack)["meaning"] == "not_configured"
        assert "No embedding service is set up" in await refused(
            client, "search", query=PARAPHRASE, mode="meaning"
        )
        assert (await ok(client, "search", query="receipt", mode="words"))["items"]
