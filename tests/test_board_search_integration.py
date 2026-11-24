"""M20's Search page against the running containers, driven in a real headless browser and checked
against a real MCP client (see ./check.sh). Search is a page (`#/search`) that the box in the top
bar goes to from every other page; a result opens the record's own page, in the Search, and the way
back is the search as it was left. What the page lists is compared with what `search` returns
(order, how each matched, the words it gives for why, which carry the scores, and where each item
stands), its counts with the server's own counts, and its paging with the server's.
Every result names itself by its reference (`story:12`, `note:7`): the page carries it as
`data-ref`, the record's address as `item=`, and the words show a code and the name (S-12 and the
title for work, AC-3 for a criterion and N-7 for a note), never #7. Titles carry a unique word,
and each test looks only at what it made, since all tests share one database."""

import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import (
    SLOW,
    board_url,
    browser_page,
    crumbs,
    current_address,
    item_address,
    item_loaded,
    made,
    no_panel_or_drawer,
    on_page,
    open_search,
    view_crumb,
)
from tests.board_search import (
    VIEWS,
    address,
    asked,
    epic_of_each,
    facet,
    on_view,
    result_rows,
    search_on_page,
    shown_triples,
    triples,
    whole_list,
)
from tests.helpers import (
    code_of,
    confirm_on_board,
    embeddings_stopped,
    make_note,
    ok,
    reindexed,
    the_token,
    unique_word,
)
from tests.helpers import number_of as number

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MATCHED = {"words": "Exact words", "meaning": "Meaning (a suggestion)", "both": "Words and meaning"}
WORK = {"epic", "story", "task"}


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


def code_shown(item: dict[str, Any]) -> str:
    """What the page writes before a result's title: S-12 for work, AC-3 for an acceptance
    criterion, N-7 for a note."""
    if item["item_type"] in WORK:
        return code_of(item["ref"])
    return f"{'AC' if item['item_type'] == 'rule' else 'N'}-{item['id']}"


def same_as_server(rows: list[dict[str, Any]], items: list[dict[str, Any]]) -> None:
    """The page lists what the server lists: the same items in the same order, each saying how it
    matched and why (the words that carry the score), and where it stands (its status chip)."""
    assert shown_triples(rows) == triples(items)
    for row, item in zip(rows, items, strict=True):
        assert row["chip"] == MATCHED[item["matched"]], item
        assert row["why"] == item["why"], item
        assert row["title"] == f"{code_shown(item)} {item['title']}", item
        # Work is named by its reference alone; an acceptance criterion or a note has its id too.
        assert row["id"] == (None if item["item_type"] in WORK else item["id"]), item
        assert row["status"] == item["status"], item


# -- exact words from every view, with the embedding service stopped -------------------------------


async def test_exact_words_are_found_from_every_page_with_the_service_stopped_and_open_pages(
    client: Client, board: str
) -> None:
    word, later = unique_word(), unique_word()
    epic = await made(client, "epic", f"Billing {word}")
    story = await made(client, "story", f"Invoices {word}", epic)
    task = await made(client, "task", f"Send them {word}", story)
    rule = await ok(client, "create_rule", title=f"Rule {word}", node=story)
    decision = await make_note(client, f"Decision {word}", decided_by="Marie", nodes=[story])
    note = await make_note(client, f"A note {word}", nodes=[story])
    confirm_on_board("note", note)
    await reindexed(client)
    on_server = await asked(client, word, "words")
    assert {(i["item_type"], i["ref"]) for i in on_server["items"]} == {
        ("epic", epic),
        ("story", story),
        ("task", task),
        ("rule", f"rule:{rule['id']}"),
        ("note", f"note:{decision['id']}"),
        ("note", f"note:{note['id']}"),
    }
    # Work has no id of its own in the answer; the other kinds keep theirs next to the reference.
    for item in on_server["items"]:
        assert ("id" in item) == (item["item_type"] not in WORK), item
    titles = {item["ref"]: item["title"] for item in on_server["items"]}
    before = (await ok(client, "server_status"))["counts"]
    head = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        with embeddings_stopped():
            # A story made while the service is down waits to be indexed; words find it at once.
            late = await made(client, "story", f"Made while the service was down {later}", epic)
            starts = [(view, f"#/{view}") for view in VIEWS]
            starts.append(("a record's page", item_address(story, "roadmap")))
            for place, start in starts:
                # The box in the top bar goes to the Search page from wherever the person is.
                await w.open(start)
                if start.startswith("#/item"):
                    await item_loaded(page, story)
                else:
                    await on_view(page, place)
                await page.locator("#global-search").fill(later)
                await page.keyboard.press("Enter")
                await on_page(page, "search")
                name, choices = await address(page)
                assert name == "search" and choices["search"] == later, (place, choices)
                assert {"drawer", "sel", "panel", "item", "from"}.isdisjoint(choices), choices
                await no_panel_or_drawer(page)
                rows = await result_rows(page, 1)
                assert shown_triples(rows) == [("story", late, "words")]
                on_the_server = await asked(client, later)
                assert shown_triples(rows) == triples(on_the_server["items"])
                assert (
                    await page.locator("#search-results tr.result mark").first.inner_text() == later
                )
                await expect(page.locator("#search-waiting")).to_contain_text(
                    "1 item waiting to be indexed"
                )
                await expect(page.locator("#index-state")).to_contain_text("1 waiting")
                assert await crumbs(page) == ["Search"], await crumbs(page)
                # The search is always for words and meaning together. Meaning cannot be used now,
                # so the page says so, in the server's words, and lists what the words found.
                assert on_the_server["index"]["meaning"] == "service_unreachable"
                await expect(page.locator("#search-note")).to_contain_text(
                    on_the_server["index"]["note"], timeout=SLOW
                )
                assert await page.locator("#search-problem").inner_text() == ""

                # A question in other words finds only what its words find, and the page lists
                # exactly what the server lists for the same words, with the same note.
                paraphrase = "Clients get a bill by mail"
                await search_on_page(page, paraphrase)
                by_both = await asked(client, paraphrase)
                assert by_both["index"]["meaning"] == "service_unreachable"
                await expect(page.locator("#search-note")).to_contain_text(
                    by_both["index"]["note"], timeout=SLOW
                )
                rows = await result_rows(page, by_both["total"])
                assert shown_triples(rows) == triples(by_both["items"])
                assert {how for _, _, how in shown_triples(rows)} <= {"words"}
                assert await page.locator("#search-problem").inner_text() == ""

            # Every kind of result opens its own page, in the Search; the way back is the search as
            # it was left, with the same results. With the service stopped, the search for the
            # word gives what the exact-words search gave before.
            await search_on_page(page, word)
            rows = await result_rows(page, 6)
            now_on_server = await asked(client, word)
            assert triples(now_on_server["items"]) == triples(on_server["items"])
            same_as_server(rows, now_on_server["items"])
            searched = await address(page)
            for row in rows:
                result = f"#search-results tr.result[data-ref='{row['ref']}']"
                await page.locator(f"{result} a.result-title").click()
                wanted = row["ref"]
                await item_loaded(page, wanted)
                name, choices = await address(page)
                assert (name, choices["item"], choices["from"]) == ("item", wanted, "search")
                await expect(page.locator("#item-title")).to_contain_text(titles[wanted])
                assert (await crumbs(page))[0] == "Search"
                await no_panel_or_drawer(page)
                await view_crumb(page).click()
                await on_page(page, "search")
                assert await address(page) == searched
                assert len(await result_rows(page, 6)) == 6
            # A click on the row, away from the title, opens the page too.
            await page.locator(f"#search-results tr.result[data-ref='{task}'] .cell-why").click()
            await item_loaded(page, task)
            await page.go_back()
            await on_page(page, "search")
            assert len(await result_rows(page, 6)) == 6
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


# -- meaning, the scores, the statuses, the counts and the paging ----------------------------------


async def test_meaning_results_equal_mcps_with_scores_statuses_counts_paging_and_the_filters_stay(
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
        title=f"The customer gets an invoice by email {word}",
        node=story,
    )
    decision = await make_note(
        client, f"Invoices go to the customer by email {word}", decided_by="Marie", nodes=[story]
    )
    confirm_on_board("note", decision)
    note = await make_note(client, f"Customers get their invoice by email {word}", nodes=[task])
    for count in range(55):
        await made(client, "task", f"Filler {count} {word}", story)
    await reindexed(client)
    epics = await epic_of_each(client)

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/roadmap")
        # There is no Search button in the top bar or on the page: Enter in the box asks, and
        # with nothing typed it goes to the Search page to ask there.
        await expect(page.locator("#global-search-go")).to_have_count(0)
        await page.locator("#global-search").press("Enter")
        await on_page(page, "search")
        await expect(page.locator("#search-box")).to_be_visible()
        await expect(page.locator("#search-go")).to_have_count(0)
        await expect(page.locator("#search-waiting")).to_have_attribute("data-waiting", "0")

        # In other words than the ones written: the same items, in the same order, with the
        # server's scores and statuses.
        await search_on_page(page, paraphrase)
        by_meaning = await asked(client, paraphrase)
        mine = {i["ref"] for i in by_meaning["items"]}
        assert {
            story,
            other,
            f"note:{decision['id']}",
            f"note:{note['id']}",
            f"rule:{rule['id']}",
        } <= mine
        rows = await whole_list(page, by_meaning["total"])
        same_as_server(rows, by_meaning["items"])
        # Nothing written here has the words of the question, so what it finds is found by
        # meaning (an item of another test that has all its words would be found both ways).
        assert all(
            ("similar in meaning (score " in row["why"]) == (row["matched"] != "words")
            for row in rows
        )
        by_ref = {row["ref"]: row for row in rows}
        for ref in (story, other, f"note:{decision['id']}", f"note:{note['id']}"):
            assert by_ref[ref]["matched"] == "meaning", by_ref[ref]
        # A note has one status, read from its chip: confirmed by a person, or not yet.
        assert by_ref[f"note:{decision['id']}"]["status_words"] == "Confirmed"
        assert by_ref[f"note:{note['id']}"]["status_words"] == "Unconfirmed"
        await expect(page.locator("#search-count")).to_have_text(f"{by_meaning['total']} results")
        # The counts by kind and by status are the server's, and the epics are the graph's: the
        # page counts an epic by its number.
        assert await facet(page, "type") == by_meaning["counts_by_type"]
        assert await facet(page, "status") == by_meaning["counts_by_status"]
        per_epic: dict[str, int] = {}
        for item in by_meaning["items"]:
            found = epics.get(item["ref"])
            if found is not None:
                per_epic[str(number(found))] = per_epic.get(str(number(found)), 0) + 1
        assert await facet(page, "epic") == per_epic

        # For the word the page shows fifty and asks for the rest: the server's paging, walked in
        # small pages, gives the same list.
        await search_on_page(page, word)
        small = await asked(client, word, size=20)
        big = await asked(client, word)
        assert small["items"] == big["items"] and small["total"] == big["total"] == 55 + 8
        await expect(page.locator("#search-count")).to_have_text("63 results", timeout=SLOW)
        assert await page.locator("#search-results tr.result").count() == 50
        await expect(page.locator("#show-more")).to_have_text("Show 13 more")
        same_as_server(await whole_list(page, 63), big["items"])
        assert await facet(page, "type") == big["counts_by_type"]
        assert await facet(page, "status") == big["counts_by_status"]

        # A kind keeps the server's order and says how many of how many; the address holds it.
        await page.locator("#search-facets [data-facet=type] button[data-value=task]").click()
        tasks = [i for i in big["items"] if i["item_type"] == "task"]
        assert len(tasks) == 56
        await expect(page.locator("#search-count")).to_have_text("56 results of 63", timeout=SLOW)
        assert (await address(page))[1]["search_type"] == "task"
        same_as_server(await whole_list(page, 56), tasks)
        # The counts of the other groups follow the choice.
        assert sum((await facet(page, "status")).values()) == 56
        # The state is in the address: the same page opens again exactly so.
        await page.reload()
        await page.wait_for_selector("#search-results tr.result", timeout=SLOW)
        await expect(page.locator("#search-box")).to_have_value(word)
        await expect(page.locator("#search-facets [data-facet=type] button.on")).to_have_text(
            "Task (56)"
        )
        same_as_server(await whole_list(page, 56), tasks)
        # An epic narrows the list further, and the choice is taken off by choosing it again.
        await page.locator("#search-facets [data-facet=type] button.on").click()
        await expect(page.locator("#search-count")).to_have_text("63 results", timeout=SLOW)
        epic_number = str(number(epic))
        await page.locator(
            f"#search-facets [data-facet=epic] button[data-value='{epic_number}']"
        ).click()
        under = [i for i in big["items"] if epics.get(i["ref"]) == epic]
        await expect(page.locator("#search-count")).to_have_text(
            f"{len(under)} results of 63", timeout=SLOW
        )
        same_as_server(await whole_list(page, len(under)), under)
        shown_now = {r["ref"] for r in await result_rows(page, len(under))}
        assert other not in shown_now and story in shown_now
        # Notes belong to no epic, so the page says they are left out.
        await expect(page.locator("#search-facets [data-facet=epic]")).to_contain_text(
            "Notes belong to no epic"
        )
        # Opening a result goes to its page, in the Search; the way back keeps the choices.
        await page.locator(f"#search-results tr.result[data-ref='{story}'] a.result-title").click()
        await item_loaded(page, story)
        await expect(page.locator("#item-title")).to_contain_text(
            f"{code_of(story)} Send the invoice", timeout=SLOW
        )
        assert (await current_address(page))[1]["from"] == "search"
        assert (await crumbs(page))[0] == "Search"
        await view_crumb(page).click()
        await on_page(page, "search")
        choices = (await address(page))[1]
        assert choices["search_epic"] == epic_number and "sel" not in choices
        assert choices["search"] == word and "search_mode" not in choices
        await expect(page.locator("#search-count")).to_have_text(
            f"{len(under)} results of 63", timeout=SLOW
        )
        assert shown_triples(await whole_list(page, len(under))) == triples(under)
        await no_panel_or_drawer(page)
        # There is no choice of how to search, and an old address that names a mode is ignored:
        # the search is the same one, for words and meaning together.
        await expect(page.locator("#search-mode")).to_have_count(0)
        await w.go(f"#/search?search={word}&search_mode=meaning")
        await expect(page.locator("#search-count")).to_have_text("63 results", timeout=SLOW)
        same_as_server(await whole_list(page, 63), big["items"])
        assert w.problems == []


# -- names: a result's parent, the epic facet and a written reference -----------------------------


async def test_a_result_names_its_parent_and_the_epic_and_a_written_reference_is_read_as_a_name(
    client: Client, board: str
) -> None:
    word, inside = unique_word(), unique_word()
    epic = await made(client, "epic", f"Naming epic {word}")
    story = await made(client, "story", f"Naming story {word}", epic)
    note = await make_note(client, f"The headline of the note {word}\n\nMore words", nodes=[story])
    task = await made(
        client,
        "task",
        f"Naming task {word}",
        story,
        body=f"See [[note:{note['id']}]] and [[story:99999]] for {inside}",
    )
    await reindexed(client)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/board")
        await open_search(page, inside)
        row = page.locator(f"#search-results tr.result[data-ref='{task}']")
        await expect(row).to_be_visible(timeout=SLOW)
        # The item is its code and its name, and what it sits under is the kind, code and name.
        assert await row.locator("a.result-title").inner_text() == (
            f"{code_of(task)} Naming task {word}"
        )
        assert await row.locator(".result-parent").inner_text() == (
            f"in Story {code_of(story)} Naming story {word}"
        )
        # A reference written in the text is read as the code and headline of what it names (an
        # item the board does not know is "an item"), never as the reference itself.
        snippet = await row.locator(".snippet").inner_text()
        assert f"N-{note['id']} The headline of the note {word}" in snippet, snippet
        assert "an item" in snippet and "[[" not in snippet, snippet
        # The epic facet is its code and name, with its count.
        await open_search(page, word)
        await expect(page.locator("#search-count")).to_contain_text("results", timeout=SLOW)
        facet_button = page.locator(
            f"#search-facets [data-facet=epic] button[data-value='{number(epic)}']"
        )
        await expect(facet_button.locator(".facet-label")).to_have_text(
            f"{code_of(epic)} Naming epic {word}"
        )
        # No `#3` anywhere on the page.
        text = await page.locator("#page").inner_text()
        assert not re.search(r"(?<![\w&])#\d+\b", text), text
        assert w.problems == []
