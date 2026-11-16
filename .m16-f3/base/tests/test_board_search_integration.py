"""M13's Search page and its Ask box against the running containers, driven in a real headless
browser and checked against a real MCP client (see ./check.sh). What the page lists is compared with
what `search` returns, its counts with what the list tools hold, and what Ask shows with what
`reflect` returns. Titles carry a unique word, and each test looks only at what it made, since all
tests share one database."""

import os
import re

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import (
    SLOW,
    asked,
    board_url,
    browser_page,
    facet,
    listed,
    made,
    plain,
    search_for,
)
from tests.helpers import (
    ScriptedModel,
    connect,
    embeddings_stopped,
    make_belief,
    make_decision,
    make_fact,
    ok,
    refused,
    reindexed,
    the_token,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

ADA = "Ada Lovelace"


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


# -- 1: words at once, meaning when the service is back ---------------------------------------


async def test_words_are_found_with_the_service_stopped_and_meaning_when_it_is_back(
    client: Client, board: str
) -> None:
    word, later = unique_word(), unique_word()
    epic = await made(client, "epic", f"Billing {word}")
    story = await made(client, "story", f"Send the invoice to the customer by email {word}", epic)
    await reindexed(client)
    # None of its words is in the story, so only meaning can find it.
    paraphrase = "Clients get a bill by mail"
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/search")
        await expect(page.locator("#waiting")).to_contain_text("0 items waiting", timeout=SLOW)

        with embeddings_stopped():
            # A person adds a phrase to the story on the board, while the service is down.
            await w.go(f"#/board?epic={epic}&card={story}")
            await page.locator("#edit-body").fill(f"The phrase {later} appears here")
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator("#panel .card-detail")).to_contain_text(
                f"The phrase {later}", timeout=SLOW
            )
            # The board hears of it, and the page says one item waits to be indexed.
            await w.go("#/search")
            await expect(page.locator("#waiting")).to_contain_text("1 item waiting", timeout=SLOW)
            await search_for(page, later, "words")
            on_page = await listed(page, 1)
            assert on_page == await asked(client, later, "words") == [("story", story, "words")]
            assert await page.locator("li.result .snippet mark").first.inner_text() == later
            await expect(page.locator("li.result .chip.matched")).to_have_text("Exact words")

            # By meaning it cannot be done now, and the page says so in the server's words.
            await search_for(page, paraphrase, "meaning")
            await expect(page.locator("#search-problem")).to_contain_text(
                "Searching by meaning is not possible now", timeout=SLOW
            )
            plain(
                await page.locator("#search-problem").inner_text(),
                await refused(client, "search", query=paraphrase, mode="meaning"),
            )
            assert await page.locator("li.result").count() == 0

        await reindexed(client)
        await expect(page.locator("#waiting")).to_contain_text("0 items waiting", timeout=SLOW)
        await search_for(page, paraphrase, "meaning")
        by_meaning = await asked(client, paraphrase, "meaning")
        assert ("story", story, "meaning") in by_meaning
        shown = await listed(page, len(by_meaning))
        assert shown == by_meaning[: len(shown)]
        assert ("story", story, "meaning") in shown
        result = page.locator(f"li.result[data-type=story][data-id='{story}']")
        await expect(result.locator(".chip.matched")).to_have_text("Meaning (a suggestion)")
        assert "similar in meaning" in await result.locator(".why").inner_text()
        assert await page.locator("#search-problem").inner_text() == ""
        assert w.problems == []


async def test_the_address_holds_the_search_and_a_result_opens_its_card(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Cards {word}")
    story = await made(client, "story", f"Findable {word}", epic)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/search?q={word}&mode=words")
        await listed(page, 2)
        assert await page.locator("#search-box").input_value() == word
        await page.locator(f"li.result[data-id='{story}'][data-type=story] a.result-title").click()
        await expect(page.locator("#panel .card-detail")).to_contain_text(f"Findable {word}")
        await w.open(f"#/search?q={word}&mode=words&type=story")
        await listed(page, 1)
        assert (await asked(client, word, "words"))[0][0] in {"epic", "story"}


# -- 2: the counts, and what the filters keep -------------------------------------------------


async def test_counts_equal_the_list_tools_and_the_filters_keep_the_servers_order(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Counted {word}")
    other_epic = await made(client, "epic", f"Elsewhere {word}")
    story = await made(client, "story", f"First story {word}", epic)
    story_b = await made(client, "story", f"Second story {word}", other_epic)
    tasks = [await made(client, "task", f"Task {n} {word}", story) for n in range(3)]
    await ok(client, "update_node", actor="a", node_id=tasks[0], status="done", expected_version=1)
    await ok(
        client, "update_node", actor="a", node_id=tasks[1], status="blocked", expected_version=1
    )
    rule = await ok(client, "create_rule", actor="a", title=f"Rule {word}", story_id=story)
    decision = await make_decision(client, f"Decision {word}")
    fact = await make_fact(client, f"A fact about {word}")
    belief = await make_belief(client, [fact], f"A belief about {word}")

    server = await ok(client, "search", query=word, mode="words", limit=200)
    by_server = [(item["item_type"], item["id"]) for item in server["items"]]
    assert len(by_server) == 11

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/search?q={word}&mode=words")
        shown = await listed(page, 11)
        assert [(kind, ident) for kind, ident, _ in shown] == by_server

        kinds = await facet(page, "type")
        statuses = await facet(page, "status")
        assert kinds == {
            "Epic": 2,
            "Story": 2,
            "Task": 3,
            "Rule": 1,
            "Decision": 1,
            "Fact": 1,
            "Belief": 1,
        }
        # The counts are the server's own counts, and the list tools' counts.
        assert {k.lower(): v for k, v in kinds.items()} == server["counts_by_type"]
        assert sorted(statuses.values()) == sorted(server["counts_by_status"].values())
        for kind, label in (("epic", "Epic"), ("story", "Story"), ("task", "Task")):
            listing = await ok(client, "list_nodes", query=word, kind=kind, limit=200)
            assert kinds[label] == len(listing["items"])
        for status, label in (("done", "Done"), ("blocked", "Blocked"), ("new", "New")):
            listing = await ok(client, "list_nodes", query=word, status=status, limit=200)
            assert statuses.get(label, 0) == len(listing["items"])
        assert statuses["Done"] == 1 and statuses["Blocked"] == 1
        assert rule["id"] in [r["id"] for r in (await ok(client, "list_rules", limit=200))["items"]]
        assert decision["id"] in [
            d["id"] for d in (await ok(client, "list_decisions", limit=200))["items"]
        ]
        listed_memories = {m["id"] for m in (await ok(client, "list_memories", limit=200))["items"]}
        assert {fact["id"], belief["id"]} <= listed_memories
        # Choosing a kind keeps the server's order, and says how many of how many.
        await page.locator("fieldset.facet[data-facet=type] button", has_text="Task").click()
        kept = await listed(page, 3)
        assert [(kind, ident) for kind, ident, _ in kept] == [
            pair for pair in by_server if pair[0] == "task"
        ]
        await expect(page.locator("#count")).to_have_text("3 results of 11.")
        # The counts of the other groups follow the choice.
        assert sum((await facet(page, "status")).values()) == 3
        assert (await page.evaluate("location.hash")).endswith("type=task")
        await page.locator("fieldset.facet[data-facet=type] button.on").click()
        await listed(page, 11)

        # An epic keeps its stories, their tasks and their rules, and drops what has no epic.
        await page.locator("fieldset.facet[data-facet=epic] button", has_text=f"#{epic} ").click()
        under = await listed(page, 1 + 1 + 3 + 1)
        assert {(kind, ident) for kind, ident, _ in under} == {
            ("epic", epic),
            ("story", story),
            *{("task", t) for t in tasks},
            ("rule", rule["id"]),
        }
        assert ("story", story_b) not in {(kind, ident) for kind, ident, _ in under}
        assert w.problems == []


# -- 7: Ask ------------------------------------------------------------------------------------


async def test_ask_shows_a_generated_answer_with_what_it_relied_on_and_records_nothing(
    client: Client, board: str, model: ScriptedModel
) -> None:
    marker = unique_word()
    epic = await made(client, "epic", f"Ask {marker}")
    decision = await make_decision(
        client, f"Use PDF {marker}", text=f"Invoices {marker} are PDFs", node_ids=[epic]
    )
    fact = await make_fact(client, f"The {marker} library is at version two")
    question = f"What format are the {marker} invoices?"
    model.script(
        marker,
        {
            "answer": f"{marker} invoices are PDFs [decision:{decision['id']}], on library two.",
            "cited": [f"decision:{decision['id']}", f"fact:{fact['id']}"],
        },
    )
    async with browser_page(board, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/search")
        before = {
            "decisions": len((await ok(client, "list_decisions", limit=200))["items"]),
            "memories": len((await ok(client, "list_memories", limit=200))["items"]),
        }
        await expect(page.locator("#ask")).to_contain_text("scripted-model")
        await page.locator("#ask-question").fill(question)
        await page.locator("#ask-send").click()
        answer = page.locator("#answer")
        await expect(answer).to_be_visible(timeout=SLOW)
        await expect(answer.locator(".chip.generated")).to_have_text("Generated, not a record")
        await expect(answer).to_contain_text(f"{marker} invoices are PDFs")
        reflected = await ok(client, "reflect", actor="check", question=question)
        relied = await page.locator("#relied-on li").evaluate_all(
            "els => els.map(e => e.dataset.relied)"
        )
        assert relied == [f"{r['item_type']}:{r['id']}" for r in reflected["relied_on"]]
        assert relied == [f"decision:{decision['id']}", f"fact:{fact['id']}"]
        await expect(page.locator("#relied-on a").first).to_contain_text(f"#{decision['id']}")
        # Asking stored nothing.
        assert before == {
            "decisions": len((await ok(client, "list_decisions", limit=200))["items"]),
            "memories": len((await ok(client, "list_memories", limit=200))["items"]),
        }

        # A person can record a belief under the typed name, resting on what it relied on.
        await page.locator("#belief-text").fill(f"{marker} invoices are PDFs, we think")
        await page.locator("#record-belief-from-answer").click()
        recorded = page.locator("#recorded-belief")
        await expect(recorded).to_be_visible(timeout=SLOW)
        belief_id = int(re.search(r"belief #(\d+)", await recorded.inner_text()).group(1))  # type: ignore[union-attr]
        belief = await ok(client, "get_memory", memory_id=belief_id)
        assert belief["type"] == "belief" and belief["origin"] == "stated"
        assert belief["recorded_by"] == ADA
        assert belief["text"] == f"{marker} invoices are PDFs, we think"
        assert {(e["type"], e["id"]) for e in belief["evidence"]} == {
            ("decision", decision["id"]),
            ("fact", fact["id"]),
        }
        assert w.problems == []


async def test_ask_says_so_when_the_server_has_no_language_model(board: str) -> None:
    url = os.environ.get("VRUM_NO_MODEL_TEST_URL", "")
    assert url, "Run the integration tests through ./check.sh."
    async with connect(url, the_token()) as client:
        assert "language model" in await refused(client, "reflect", actor="a", question="Why?")
    async with browser_page(board_url(url), token=the_token()) as w:
        await w.open("#/search")
        await expect(w.page.locator("#ask-none")).to_contain_text(
            "No language model is set up on this server"
        )
        assert await w.page.locator("#ask-question").count() == 0
        assert w.problems == []
