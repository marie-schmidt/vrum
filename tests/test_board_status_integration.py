"""M14's Status page against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh): what it shows equals `server_status` and the index state
the searches report, the embedding service is seen answering and then not, the downloaded export
is the MCP export, and the token and the database password are in no page and no response."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import SLOW, board_url, browser_page, made
from tests.helpers import (
    embeddings_stopped,
    export_everything,
    make_decision,
    make_fact,
    ok,
    reindexed,
    the_token,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

COUNTS = (
    "epics",
    "stories",
    "tasks",
    "links",
    "rules",
    "decisions",
    "principles",
    "facts",
    "beliefs",
    "events",
)


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def shown(page: Page, name: str) -> str:
    """The value on one row of the Status page."""
    return (await page.locator(f"#status-body tr[data-name={name}] .value").inner_text()).strip()


# -- 4: what the page shows is what the server says ----------------------------------------------


async def test_status_equals_server_status_and_the_index_state_through_mcp(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Counted {word}")
    await made(client, "story", f"A story {word}", epic)
    await make_decision(client, f"A decision {word}")
    await make_fact(client, f"A fact {word}")
    await reindexed(client)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/status")
        await expect(page.locator("#versions")).to_be_visible(timeout=SLOW)
        status = await ok(client, "server_status")
        said = (await ok(client, "search", query=word, limit=1))["index"]

        assert await shown(page, "vrum_version") == status["vrum_version"]
        assert await shown(page, "postgres_version") == status["postgres_version"]
        assert await shown(page, "pgvector_version") == status["pgvector_version"]
        # The size of every kind of item.
        for name in COUNTS:
            assert await shown(page, name) == str(status["counts"][name]), name
        # The index, as the server and a search both say it.
        index = status["index"]
        assert (index["meaning"], index["waiting_to_index"]) == (
            said["meaning"],
            said["waiting_to_index"],
        )
        assert await shown(page, "meaning") == "In use"
        assert await shown(page, "waiting") == str(said["waiting_to_index"]) == "0"
        assert await shown(page, "indexed") == str(index["indexed"])
        assert await shown(page, "service") == "Answering"
        assert await shown(page, "model") == index["embedding_model"] == "scripted-embedding"
        assert await shown(page, "vector_size") == str(index["vector_size"])
        last = index["last_reindex"]
        assert last is not None
        text = await shown(page, "last_reindex")
        assert (
            f"{last['embedded']} item" in text and f"{last['waiting_after']} still waiting" in text
        )
        # The language model is named, never keyed.
        await expect(page.locator("#language-model")).to_contain_text(status["language_model"])
        assert w.problems == []


async def test_a_stopped_embedding_service_and_waiting_items_are_seen_on_the_page(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Waiting {word}")
    await reindexed(client)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/status")
        await expect(page.locator("#status-body tr[data-name=service] .value")).to_have_text(
            "Answering", timeout=SLOW
        )
        before = int(await shown(page, "waiting"))
        with embeddings_stopped():
            await made(client, "story", f"Not indexed {word}", epic)
            await w.go("#/home")
            await w.go("#/status")
            await expect(page.locator("#status-body tr[data-name=service] .value")).to_have_text(
                "Not answering", timeout=SLOW
            )
            await expect(page.locator("#status-body tr[data-name=waiting] .value")).to_have_text(
                str(before + 1), timeout=SLOW
            )
            status = await ok(client, "server_status")
            assert status["index"]["embedding_service"] == "unreachable"
            assert status["index"]["waiting_to_index"] == before + 1
        await reindexed(client)
        await w.go("#/home")
        await w.go("#/status")
        await expect(page.locator("#status-body tr[data-name=service] .value")).to_have_text(
            "Answering", timeout=SLOW
        )
        await expect(page.locator("#status-body tr[data-name=waiting] .value")).to_have_text(
            "0", timeout=SLOW
        )
        assert w.problems == []


async def test_a_server_with_no_language_model_says_so(board: str) -> None:
    url = os.environ.get("VRUM_NO_MODEL_TEST_URL", "")
    assert url, "Run the integration tests through ./check.sh."
    async with browser_page(board_url(url), token=the_token()) as w:
        await w.open("#/status")
        await expect(w.page.locator("#language-model")).to_contain_text(
            "None is set up", timeout=SLOW
        )
        assert w.problems == []


# -- 4: the export, and what is never shown ------------------------------------------------------


async def test_the_downloaded_export_is_the_mcp_export(
    client: Client, board: str, tmp_path: Path
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Exported {word}")
    story = await made(client, "story", f"Story {word}", epic)
    await made(client, "task", f"Task {word}", story)
    await make_decision(client, f"Decision {word}", node_ids=[story])
    fact = await make_fact(client, f"A fact {word}")
    await ok(
        client, "record_belief", actor="a", text=f"A belief {word}", evidence_fact_ids=[fact["id"]]
    )
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/status")
        await expect(page.locator("#download-export")).to_be_visible(timeout=SLOW)
        async with page.expect_download() as waiting:
            await page.locator("#download-export").click()
        download = await waiting.value
        path = tmp_path / "export.json"
        await download.save_as(path)
        assert download.suggested_filename.startswith("vrum-export-")
        assert download.suggested_filename.endswith(".json")
        got: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        mcp = await export_everything(client)
        assert got == mcp
        assert got["format"] and got["counts"]["nodes"] == len(got["nodes"]) >= 3
        await expect(page.locator("#export-note")).to_contain_text("Downloaded:", timeout=SLOW)
        await expect(page.locator("#export-note")).to_contain_text(f"{len(got['events'])} events")
        assert word in path.read_text(encoding="utf-8")
        assert w.problems == []


async def test_the_token_and_the_database_password_are_in_no_page_and_no_response(
    client: Client, board: str, tmp_path: Path
) -> None:
    secrets = {
        "the token": the_token(),
        "the database password": os.environ.get("VRUM_DB_PASSWORD", "no-such-password-set"),
    }
    assert all(len(secret) >= 16 for secret in secrets.values())
    async with browser_page(board, token=the_token(), keep_bodies=True) as w:
        page = w.page
        await w.open("#/home")
        texts = []
        for name in ("home", "activity", "tools", "status", "memory", "search"):
            await w.go(f"#/{name}")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(500)
            texts.append(await page.locator("body").inner_text())
        # Open every tool's try form that needs nothing and run the status one too.
        await w.go("#/tools")
        entry = page.locator("#tool-entry-server_status")
        await expect(entry).to_be_visible(timeout=SLOW)
        await entry.locator("details.try summary").click()
        await entry.locator(".try-run").click()
        await expect(entry.locator("pre.result")).to_be_visible(timeout=SLOW)
        texts.append(await page.locator("body").inner_text())
        async with page.expect_download() as waiting:
            await w.go("#/status")
            await page.locator("#download-export").click()
        download = await waiting.value
        path = tmp_path / "export.json"
        await download.save_as(path)
        await page.wait_for_load_state("networkidle")
        assert len(w.bodies) > 20
        for label, secret in secrets.items():
            assert [url for url, body in w.bodies if secret in body] == [], label
            assert [text for text in texts if secret in text] == [], label
            assert secret not in path.read_text(encoding="utf-8"), label
            assert [url for url in w.requests if secret in url] == [], label
            assert secret not in await page.evaluate("JSON.stringify(localStorage)"), label
            assert secret not in await page.content(), label
        # The token is kept for this tab and sent only in the header, never in an address.
        assert all(the_token() not in url for url in w.requests)
        assert w.problems == []
