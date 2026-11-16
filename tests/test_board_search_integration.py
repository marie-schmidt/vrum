"""M16's search drawer against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh). Search is a drawer that opens over Board, Graph and
Roadmap from the box in the top bar; a result opens the ordinary detail panel beside it. What the
drawer lists is compared with what `search` returns (order, how each matched, the words it gives
for why, which carry the scores, and the labels of decisions and facts), its counts with the
server's own counts, and its paging with the server's. Titles carry a unique word, and each test
looks only at what it made, since all tests share one database."""

import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import SLOW, board_url, browser_page, made, plain
from tests.board_search import (
    VIEWS,
    address,
    asked,
    drawer,
    epic_of_each,
    facet,
    on_view,
    result_rows,
    search_in_drawer,
    shown_triples,
    triples,
    whole_list,
)
from tests.helpers import (
    confirm,
    embeddings_stopped,
    make_decision,
    make_fact,
    ok,
    refused,
    reindexed,
    the_token,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MATCHED = {"words": "Exact words", "meaning": "Meaning (a suggestion)", "both": "Words and meaning"}
# What a result opens in the detail panel: the address's own word for its kind.
RECORD = {
    "epic": "node",
    "story": "node",
    "task": "node",
    "rule": "rule",
    "decision": "decision",
    "fact": "fact",
}


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


def same_as_server(rows: list[dict[str, Any]], items: list[dict[str, Any]]) -> None:
    """The drawer lists what the server lists: the same items in the same order, each saying how it
    matched and why (the words that carry the score), and a decision or a fact its label."""
    assert shown_triples(rows) == triples(items)
    for row, item in zip(rows, items, strict=True):
        assert row["chip"] == MATCHED[item["matched"]], item
        assert row["why"] == item["why"], item
        assert row["title"] == f"#{item['id']} {item['title']}", item
        assert row["label"] == (
            item["label"] if item["item_type"] in {"decision", "fact"} else None
        )


# -- exact words from every view, with the embedding service stopped -------------------------------


async def test_exact_words_are_found_from_every_view_with_the_service_stopped_and_open_a_panel(
    client: Client, board: str
) -> None:
    word, later = unique_word(), unique_word()
    epic = await made(client, "epic", f"Billing {word}")
    story = await made(client, "story", f"Invoices {word}", epic)
    task = await made(client, "task", f"Send them {word}", story)
    rule = await ok(client, "create_rule", actor="a", title=f"Rule {word}", story_id=story)
    decision = await make_decision(client, f"Decision {word}", node_ids=[story])
    fact = await make_fact(client, f"A fact {word}", node_ids=[story])
    await confirm(client, "fact", fact)
    await reindexed(client)
    on_server = await asked(client, word, "words")
    assert {(i["item_type"], i["id"]) for i in on_server["items"]} == {
        ("epic", epic),
        ("story", story),
        ("task", task),
        ("rule", rule["id"]),
        ("decision", decision["id"]),
        ("fact", fact["id"]),
    }
    before = (await ok(client, "server_status"))["counts"]
    head = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        with embeddings_stopped():
            # A story made while the service is down waits to be indexed; words find it at once.
            late = await made(client, "story", f"Made while the service was down {later}", epic)
            for view in VIEWS:
                # A record is selected before searching, and searching leaves it selected.
                await w.open(f"#/{view}?sel=node:{story}")
                await on_view(page, view)
                await expect(page.locator("#panel #card-title")).to_contain_text(f"#{story} ")
                await page.locator("#global-search").fill(later)
                await page.keyboard.press("Enter")
                await expect(drawer(page, "search")).to_be_visible(timeout=SLOW)
                name, choices = await address(page)
                assert name == view and choices["drawer"] == "search"
                assert choices["search"] == later and choices["sel"] == f"node:{story}"
                rows = await result_rows(page, 1)
                assert shown_triples(rows) == [("story", late, "words")]
                assert shown_triples(rows) == triples(
                    (await asked(client, later, "words"))["items"]
                )
                assert (
                    await page.locator("#search-results li.result mark").first.inner_text() == later
                )
                await expect(page.locator("#search-waiting")).to_contain_text(
                    "1 item waiting to be indexed"
                )
                await expect(page.locator("#index-state")).to_contain_text("1 waiting")

                # By meaning it cannot be done now, and the drawer says so in the server's words.
                paraphrase = "Clients get a bill by mail"
                await search_in_drawer(page, paraphrase, "meaning")
                await expect(page.locator("#search-problem")).to_contain_text(
                    "Searching by meaning is not possible now", timeout=SLOW
                )
                plain(
                    await page.locator("#search-problem").inner_text(),
                    await refused(client, "search", query=paraphrase, mode="meaning"),
                )
                assert await page.locator("#search-results li.result").count() == 0

                # Every kind of result: it opens in the ordinary detail panel, the drawer stays.
                await search_in_drawer(page, word, "words")
                rows = await result_rows(page, 6)
                same_as_server(rows, on_server["items"])
                for row in rows:
                    result = (
                        f"#search-results li.result[data-type={row['type']}][data-id='{row['id']}']"
                    )
                    await page.locator(f"{result} a.result-title").click()
                    wanted = f"{RECORD[row['type']]}:{row['id']}"
                    await expect(page).to_have_url(re.compile(rf"sel={wanted}(&|$)"))
                    name, choices = await address(page)
                    assert name == view and choices["sel"] == wanted
                    assert choices["drawer"] == "search" and choices["search"] == word
                    if RECORD[row["type"]] == "node":
                        await expect(page.locator("#panel #card-title")).to_contain_text(
                            row["title"], timeout=SLOW
                        )
                    else:
                        await expect(
                            page.locator(f"#panel .record-detail[data-record='{wanted}']")
                        ).to_be_visible(timeout=SLOW)
                    assert len(await result_rows(page, 6)) == 6
                await on_view(page, view)
                await expect(drawer(page, "search")).to_be_visible()
                await page.locator("#drawer-close").click()
                await expect(page.locator("#drawer")).to_be_hidden()
                assert "drawer" not in (await address(page))[1]
        await reindexed(client)
        # Searching only reads: nothing was written, and there was no change to hear of.
        assert (await ok(client, "server_status"))["counts"] == {
            **before,
            "stories": before["stories"] + 1,
            "events": before["events"] + 1,
        }
        assert (await ok(client, "changes_since", after_event_id=head, limit=200))[
            "latest_event_id"
        ] == head + 1
        assert w.problems == []


# -- meaning, the scores, the labels, the counts and the paging ------------------------------------


async def test_meaning_results_equal_mcps_with_scores_labels_counts_and_paging_and_the_filters_stay(
    client: Client, board: str
) -> None:
    word = unique_word()
    paraphrase = "Clients get a bill by mail"
    epic = await made(client, "epic", f"Billing {word}")
    elsewhere = await made(client, "epic", f"Elsewhere {word}")
    story = await made(client, "story", f"Send the invoice to the customer by email {word}", epic)
    other = await made(client, "story", f"Customers receive invoices by email {word}", elsewhere)
    task = await made(client, "task", f"Email the customer an invoice {word}", story)
    rule = await ok(
        client,
        "create_rule",
        actor="a",
        title=f"The customer gets an invoice by email {word}",
        story_id=story,
    )
    decision = await make_decision(
        client, f"Invoices go to the customer by email {word}", node_ids=[story]
    )
    await confirm(client, "decision", decision)
    fact = await make_fact(client, f"Customers get their invoice by email {word}", node_ids=[task])
    for number in range(55):
        await made(client, "task", f"Filler {number} {word}", story)
    await reindexed(client)
    epics = await epic_of_each(client)

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/roadmap")
        await page.locator("#global-search-go").click()
        await expect(drawer(page, "search")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#search-waiting")).to_contain_text("0 items waiting")

        # By meaning: the same items, in the same order, with the server's scores and labels.
        await search_in_drawer(page, paraphrase, "meaning")
        by_meaning = await asked(client, paraphrase, "meaning")
        mine = {(i["item_type"], i["id"]) for i in by_meaning["items"]}
        assert {
            ("story", story),
            ("story", other),
            ("decision", decision["id"]),
            ("fact", fact["id"]),
            ("rule", rule["id"]),
        } <= mine
        rows = await whole_list(page, by_meaning["total"])
        same_as_server(rows, by_meaning["items"])
        assert all("similar in meaning (score " in row["why"] for row in rows)
        assert all(row["matched"] == "meaning" for row in rows)
        by_id = {(row["type"], row["id"]): row for row in rows}
        assert by_id[("decision", decision["id"])]["label"].startswith(
            "Decision, confirmed by Marie"
        )
        assert by_id[("fact", fact["id"])]["label"].startswith("Fact, not confirmed by a person")
        await expect(page.locator("#search-count")).to_have_text(f"{by_meaning['total']} results.")
        # The counts by kind and by status are the server's, and the epics are the graph's.
        assert await facet(page, "type") == by_meaning["counts_by_type"]
        assert await facet(page, "status") == by_meaning["counts_by_status"]
        per_epic: dict[str, int] = {}
        for item in by_meaning["items"]:
            found = epics.get((item["item_type"], item["id"]))
            if found is not None:
                per_epic[str(found)] = per_epic.get(str(found), 0) + 1
        assert await facet(page, "epic") == per_epic

        # By words the drawer shows fifty and asks for the rest: the server's paging, walked in
        # small pages, gives the same list.
        await search_in_drawer(page, word, "words")
        small = await asked(client, word, "words", size=20)
        big = await asked(client, word, "words")
        assert small["items"] == big["items"] and small["total"] == big["total"] == 55 + 8
        await expect(page.locator("#search-count")).to_have_text("63 results.", timeout=SLOW)
        assert await page.locator("#search-results li.result").count() == 50
        await expect(page.locator("#show-more")).to_have_text("Show 13 more")
        same_as_server(await whole_list(page, 63), big["items"])
        assert await facet(page, "type") == big["counts_by_type"]
        assert await facet(page, "status") == big["counts_by_status"]

        # A kind keeps the server's order and says how many of how many; the address holds it.
        await page.locator("#search-facets [data-facet=type] button[data-value=task]").click()
        tasks = [i for i in big["items"] if i["item_type"] == "task"]
        assert len(tasks) == 56
        await expect(page.locator("#search-count")).to_have_text("56 results of 63.", timeout=SLOW)
        assert (await address(page))[1]["search_type"] == "task"
        same_as_server(await whole_list(page, 56), tasks)
        # The counts of the other groups follow the choice.
        assert sum((await facet(page, "status")).values()) == 56
        # The state is in the address: the same page opens again exactly so, with its mode.
        await page.reload()
        await page.wait_for_selector("#search-results li.result", timeout=SLOW)
        await expect(page.locator("#search-box")).to_have_value(word)
        await expect(page.locator("#search-mode")).to_have_value("words")
        await expect(page.locator("#search-facets [data-facet=type] button.on")).to_have_text(
            "Task (56)"
        )
        same_as_server(await whole_list(page, 56), tasks)
        # Opening a result keeps the choice, and an epic narrows the list further.
        await page.locator("#search-facets [data-facet=type] button.on").click()
        await expect(page.locator("#search-count")).to_have_text("63 results.", timeout=SLOW)
        await page.locator(f"#search-facets [data-facet=epic] button[data-value='{epic}']").click()
        under = [i for i in big["items"] if epics.get((i["item_type"], i["id"])) == epic]
        await expect(page.locator("#search-count")).to_have_text(
            f"{len(under)} results of 63.", timeout=SLOW
        )
        same_as_server(await whole_list(page, len(under)), under)
        shown_now = {(r["type"], r["id"]) for r in await result_rows(page, len(under))}
        assert ("story", other) not in shown_now and ("story", story) in shown_now
        # Decisions and facts belong to no epic, so the drawer says they are left out.
        await expect(page.locator("#search-facets [data-facet=epic]")).to_contain_text(
            "Decisions and facts belong to no epic"
        )
        await page.locator(
            f"#search-results li.result[data-type=story][data-id='{story}'] a.result-title"
        ).click()
        await expect(page.locator("#panel #card-title")).to_contain_text(
            f"#{story} Send the invoice", timeout=SLOW
        )
        choices = (await address(page))[1]
        assert choices["search_epic"] == str(epic) and choices["sel"] == f"node:{story}"
        assert shown_triples(await result_rows(page, len(under))) == triples(under)
        assert w.problems == []
