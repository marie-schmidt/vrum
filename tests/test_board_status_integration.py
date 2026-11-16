"""M16's Status page, one of the settings pages, against the running containers, driven in a real
headless browser and checked against a real MCP client (see ./check.sh): it is reached from the
Settings menu with the keyboard, what it shows equals `server_status` and the index state the
searches report (and names no language model, since there is none), the embedding service is seen
answering and then not, the downloaded export is the MCP export, and no agent key, session or
database password is in any page or response."""

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import SLOW, board_call, board_url, browser_page, made
from tests.board_search import open_settings
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
from tests.world import SESSION_COOKIE, project_at

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

COUNTS = ("epics", "stories", "tasks", "links", "rules", "decisions", "facts", "events")
SECTIONS = ("nodes", "links", "rules", "decisions", "facts", "events")
NO_MODEL = re.compile(r"language model|belief|principle", re.IGNORECASE)


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def shown(page: Page, name: str) -> str:
    """The value on one row of the Status page."""
    return (await page.locator(f"#status-body tr[data-name={name}] .value").inner_text()).strip()


# -- what the page shows is what the server says -------------------------------------------------


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
        await w.open("#/graph")
        await open_settings(page, "status")
        await expect(page.locator("#versions")).to_be_visible(timeout=SLOW)
        status = await ok(client, "server_status")
        said = (await ok(client, "search", query=word, limit=1))["index"]

        assert await shown(page, "vrum_version") == status["vrum_version"]
        assert await shown(page, "postgres_version") == status["postgres_version"]
        assert await shown(page, "pgvector_version") == status["pgvector_version"]
        # The size of every kind of item, which are the ones of the two context records and no
        # others.
        assert set(status["counts"]) == set(COUNTS)
        for name in COUNTS:
            assert await shown(page, name) == str(status["counts"][name]), name
        assert await page.locator("#sizes tr[data-name]").evaluate_all(
            "els => els.map(e => e.dataset.name)"
        ) == list(COUNTS)
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
        await expect(page.locator("#index-state")).to_have_text("Index: 0 waiting")
        # There is no language model: not in what the server says, not on the page.
        assert "language_model" not in status
        assert not NO_MODEL.search(await page.locator("#page").inner_text())
        assert await page.locator("#language-model").count() == 0
        info = board_call(board, "GET", "/board/api/info").body
        assert "language_model" not in json.dumps(info)
        assert w.problems == []


async def test_a_stopped_embedding_service_and_waiting_items_are_seen_on_the_page(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Waiting {word}")
    await reindexed(client)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/roadmap")
        await open_settings(page, "status")
        service = page.locator("#status-body tr[data-name=service] .value")
        await expect(service).to_have_text("Answering", timeout=SLOW)
        before = int(await shown(page, "waiting"))
        with embeddings_stopped():
            await made(client, "story", f"Not indexed {word}", epic)
            # The page says so as soon as the board hears that something waits.
            await expect(service).to_have_text("Not answering", timeout=SLOW)
            await expect(page.locator("#status-body tr[data-name=waiting] .value")).to_have_text(
                str(before + 1), timeout=SLOW
            )
            await expect(page.locator("#index-state")).to_have_text(
                f"Index: {before + 1} waiting", timeout=SLOW
            )
            status = await ok(client, "server_status")
            assert status["index"]["embedding_service"] == "unreachable"
            assert status["index"]["waiting_to_index"] == before + 1
            assert w.problems == []
        await reindexed(client)
        await expect(service).to_have_text("Answering", timeout=SLOW)
        await expect(page.locator("#status-body tr[data-name=waiting] .value")).to_have_text(
            "0", timeout=SLOW
        )
        await expect(page.locator("#index-state")).to_have_text("Index: 0 waiting", timeout=SLOW)
        assert w.problems == []


# -- the export, and what is never shown ---------------------------------------------------------


async def test_the_downloaded_export_is_the_mcp_export(
    client: Client, board: str, tmp_path: Path
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Exported {word}")
    story = await made(client, "story", f"Story {word}", epic)
    await made(client, "task", f"Task {word}", story)
    decision = await make_decision(client, f"Decision {word}", node_ids=[story])
    fact = await make_fact(
        client, f"A fact {word}", node_ids=[story], decision_ids=[decision["id"]]
    )
    await ok(
        client,
        "set_confirmation",
        actor="a",
        kind="fact",
        record_id=fact["id"],
        confirmation="confirmed",
        person="Marie",
    )
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        await open_settings(page, "status")
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
        assert set(got["counts"]) == set(SECTIONS)
        # The records come with their confirmation and what they concern.
        exported = next(f for f in got["facts"] if f["id"] == fact["id"])
        assert exported["confirmed_by"] == "Marie" and exported["confirmed_at"]
        await expect(page.locator("#export-note")).to_contain_text("Downloaded:", timeout=SLOW)
        await expect(page.locator("#export-note")).to_contain_text(f"{len(got['events'])} events")
        assert word in path.read_text(encoding="utf-8")
        assert w.problems == []


async def test_no_agent_key_no_session_and_no_database_password_is_in_any_page_or_response(
    client: Client, board: str, tmp_path: Path
) -> None:
    word = unique_word()
    project = project_at(board)
    password = os.environ.get("VRUM_DB_PASSWORD", "no-such-password-set")
    async with browser_page(board, token=the_token(), keep_bodies=True) as w:
        page = w.page
        cookie = next(c for c in await page.context.cookies() if c["name"] == SESSION_COOKIE)
        secrets = {
            "the agent key": project.key,
            "the session cookie": cookie["value"],
            "the database password": password,
        }
        assert all(len(secret) >= 16 for secret in secrets.values())
        await w.open("#/board")
        texts = []
        # Every page and every drawer, as the person would reach them.
        for address in (
            "#/board",
            "#/graph?view=context&as=list",
            "#/roadmap",
            "#/board?drawer=activity",
            f"#/graph?drawer=search&search={word}",
            "#/roadmap?drawer=search&dups=1",
            "#/access",
            "#/tools",
            "#/status",
        ):
            await w.go(address)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(500)
            if address == "#/access":
                # The keys are listed by their first letters and nothing more.
                await expect(page.locator("#keys")).to_contain_text(f"{project.key[:8]}…")
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
        # The session's own secret, which the page needs to make a change, is told to the page once
        # (by the answer that says who is signed in) and nowhere else.
        told = [json.loads(body) for url, body in w.bodies if url.endswith("/board/api/session")]
        assert told
        csrf = told[0]["csrf"]
        assert len(csrf) >= 16 and all(one["csrf"] == csrf for one in told)
        elsewhere = [url for url, body in w.bodies if csrf in body and not url.endswith("/session")]
        assert elsewhere == []
        assert [text for text in texts if csrf in text] == []
        assert csrf not in path.read_text(encoding="utf-8")
        assert [url for url in w.requests if csrf in url] == []
        assert csrf not in await page.evaluate("JSON.stringify(localStorage)")
        assert csrf not in await page.content()
        # Nothing the board asked for went anywhere but the server it came from.
        assert all(url.startswith(w.origin) for url in w.requests)
        assert w.problems == []
