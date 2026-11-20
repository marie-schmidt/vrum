"""Chapter 11 of the acceptance scenario: finding and connecting on the board (M13, M16 and M20),
driven in a real headless browser against the real containers, with what the page shows compared
with what a real MCP client gets from the same tools. Search is a page (`#/search`) that the box in
the top bar takes the person to from Board, Graph and Roadmap; a result opens the record's own page,
and the way back is the breadcrumb's Search, which returns the results as they were left. Possible
duplicates is the second tab of the same page. It is searched by exact words with the embedding
service stopped, and by meaning with it back (scores, statuses, counts and paging equal to
`search`), the pairs and scores equal to `find_overlaps`, and what a click on a pair creates
(exactly one link) compared with what a search creates (none). Markdown that is only ever text,
references and their backlinks, and the quick switcher follow. There is no Ask: Vrum has no
language model."""

import re
from typing import Any

from playwright.async_api import Error as BrowserError
from playwright.async_api import Page, expect

from tests.acceptance_scenario import URL, Failed, chapter, clip, proof
from tests.board_context import fresh_project, hash_params
from tests.board_helpers import (
    PAGES,
    SLOW,
    browser_page,
    crumbs,
    detail,
    item_address,
    item_loaded,
    item_page,
    made,
    no_panel_or_drawer,
    on_page,
    open_item,
    sidebar_current,
    view_crumb,
)
from tests.board_search import (
    DUPLICATES,
    VIEWS,
    address,
    asked,
    epic_of_each,
    every_overlap,
    facet,
    link_count,
    links_of,
    on_view,
    pair_triples,
    pairs_on,
    redrawn,
    result_rows,
    search_on_page,
    shown_triples,
    triples,
    twin_epics,
    whole_list,
)
from tests.helpers import (
    code_of,
    confirm_on_board,
    embeddings_stopped,
    make_note,
    number_of,
    ok,
    reindexed,
    unique_word,
)
from tests.test_board_references_integration import BODY
from tests.test_board_search_integration import WORK, same_as_server

ADA = "Ada Lovelace"


async def searching() -> None:
    chapter("Finding and connecting: search from every view, possible duplicates, references")
    try:
        await exact_words_from_every_view()
        await meaning_and_paging()
        await possible_duplicates()
        await marking_links()
        await markdown_is_text()
        await references()
        await switching()
    except BrowserError as error:
        raise Failed(
            f"M16: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- exact words, from every view, with the embedding service stopped -----------------------


async def exact_words_from_every_view() -> None:
    word, later = unique_word(), unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Billing {word}")
        story = await made(client, "story", f"Invoices {word}", epic)
        task = await made(client, "task", f"Send them {word}", story)
        rule = await ok(client, "create_rule", actor="a", title=f"Rule {word}", node=story)
        note = await make_note(client, f"A note {word}", nodes=[story])
        decision = await make_note(
            client, f"A decision {word}", "Because", decided_by="Marie", nodes=[story]
        )
        confirm_on_board("note", decision, project=project)
        await reindexed(client)
        on_server = await asked(client, word, "words")
        wanted = {
            epic,
            story,
            task,
            f"rule:{rule['id']}",
            f"note:{note['id']}",
            f"note:{decision['id']}",
        }
        before = (await ok(client, "server_status"))["counts"]
        head = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]
        opened = []
        async with browser_page(board, token=project.key) as w:
            page = w.page
            with embeddings_stopped():
                late = await made(client, "story", f"Made while the service was down {later}", epic)
                said_so = True
                for view in VIEWS:
                    await w.open(f"#/{view}")
                    await on_view(page, view)
                    await page.locator("#global-search").fill(later)
                    await page.keyboard.press("Enter")
                    await on_page(page, "search")
                    name, choices = await address(page)
                    assert name == "search" and choices == {"search": later}
                    await no_panel_or_drawer(page)
                    rows = await result_rows(page, 1)
                    assert shown_triples(rows) == [("story", late, "words")]
                    on_the_server = await asked(client, later)
                    assert shown_triples(rows) == triples(on_the_server["items"])
                    await expect(page.locator("#search-waiting")).to_contain_text(
                        "1 item waiting to be indexed"
                    )
                    # Meaning cannot be used now, and the page says so in the server's words.
                    await expect(page.locator("#search-note")).to_contain_text(
                        on_the_server["index"]["note"], timeout=SLOW
                    )
                    paraphrase = "Clients get a bill by mail"
                    await search_on_page(page, paraphrase)
                    by_both = await asked(client, paraphrase)
                    await expect(page.locator("#search-note")).to_contain_text(
                        by_both["index"]["note"], timeout=SLOW
                    )
                    said_so = said_so and (
                        on_the_server["index"]["meaning"] == "service_unreachable"
                        and by_both["index"]["meaning"] == "service_unreachable"
                        and shown_triples(await result_rows(page, by_both["total"]))
                        == triples(by_both["items"])
                        and await page.locator("#search-problem").inner_text() == ""
                    )
                    await search_on_page(page, word)
                    rows = await result_rows(page, 6)
                    now_on_server = await asked(client, word)
                    same_as_server(rows, now_on_server["items"])
                    said_so = said_so and triples(now_on_server["items"]) == triples(
                        on_server["items"]
                    )
                    for row in rows:
                        result = f"#search-results tr.result[data-ref='{row['ref']}']"
                        await page.locator(f"{result} a.result-title").click()
                        record = row["ref"]
                        await expect(page).to_have_url(
                            re.compile(rf"#/item\?item={record}&from=search$")
                        )
                        await item_loaded(page, record)
                        await expect(page.locator("#item-title")).to_contain_text(
                            row["title"].split(" ", 1)[-1], timeout=SLOW
                        )
                        await no_panel_or_drawer(page)
                        path = await crumbs(page)
                        assert path[0] == "Projects" and path[2] == "Search", path
                        assert await sidebar_current(page) == [], record
                        # The way back is the Search page as it was left.
                        await view_crumb(page).click()
                        await on_page(page, "search")
                        assert len(await result_rows(page, 6)) == 6
                    opened.append(view)
            await reindexed(client)
            after = (await ok(client, "server_status"))["counts"]
            news = (await ok(client, "changes_since", after_event_id=head, limit=200))[
                "latest_event_id"
            ]
            assert w.problems == []
    proof(
        "M20",
        "from each of Board, Graph and Roadmap, with the embedding service stopped, the search "
        "box takes the person to the Search page, which finds by exact words what search finds "
        "(the search is always for words and meaning together), labelled exact words, a story "
        "made while the service was down at once (one item waiting), says in the server's words "
        "that meaning could not be used, and a result of "
        "every kind opens its own page (its breadcrumb starts Projects, the project, Search) from "
        "which the breadcrumb's Search returns the results as they were; merely searching "
        "writes nothing. Every result names itself by its reference (story:12, note:7), and "
        "only requirements and notes also have an id",
        {i["ref"] for i in on_server["items"]} == wanted
        and said_so
        and all(("id" in i) == (i["item_type"] not in WORK) for i in on_server["items"])
        and opened == list(VIEWS)
        and after == {**before, "stories": before["stories"] + 1, "events": before["events"] + 1}
        and news == head + 1,
        f"{len(opened)} views, six results each",
    )


# -- meaning, with the scores, the statuses, the counts and the paging -----------------------


async def meaning_and_paging() -> None:
    word = unique_word()
    paraphrase = "Clients get a bill by mail"
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Billing {word}")
        elsewhere = await made(client, "epic", f"Elsewhere {word}")
        story = await made(
            client, "story", f"Send the invoice to the customer by email {word}", epic
        )
        other = await made(
            client, "story", f"Customers receive invoices by email {word}", elsewhere
        )
        task = await made(client, "task", f"Email the customer an invoice {word}", story)
        rule = await ok(
            client,
            "create_rule",
            actor="a",
            title=f"The customer gets an invoice by email {word}",
            node=story,
        )
        decision = await make_note(
            client,
            f"Invoices go to the customer by email {word}",
            "Because",
            decided_by="Marie",
            nodes=[story],
        )
        confirm_on_board("note", decision, project=project)
        note = await make_note(
            client, f"Customers get their invoice by email {word}", "Because", nodes=[task]
        )
        for count in range(55):
            await made(client, "task", f"Filler {count} {word}", story)
        await reindexed(client)
        epics = await epic_of_each(client)
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await w.open("#/roadmap")
            await page.locator("#global-search-go").click()
            await on_page(page, "search")
            await expect(page.locator("#search-waiting")).to_have_attribute("data-waiting", "0")
            await search_on_page(page, paraphrase)
            by_meaning = await asked(client, paraphrase)
            mine = {i["ref"] for i in by_meaning["items"]}
            rows = await whole_list(page, by_meaning["total"])
            same_as_server(rows, by_meaning["items"])
            by_ref = {row["ref"]: row for row in rows}
            statuses_hold = (
                by_ref[f"note:{decision['id']}"]["status_words"] == "Confirmed"
                and by_ref[f"note:{note['id']}"]["status_words"] == "Unconfirmed"
            )
            scored = all(
                ("similar in meaning (score " in row["why"]) == (row["matched"] != "words")
                for row in rows
            ) and all(
                by_ref[ref]["matched"] == "meaning"
                for ref in (story, other, f"note:{decision['id']}", f"note:{note['id']}")
            )
            by_type = await facet(page, "type") == by_meaning["counts_by_type"]
            by_status = await facet(page, "status") == by_meaning["counts_by_status"]
            # The page counts an epic by its number.
            per_epic: dict[str, int] = {}
            for item in by_meaning["items"]:
                found = epics.get(item["ref"])
                if found is not None:
                    key = str(number_of(found))
                    per_epic[key] = per_epic.get(key, 0) + 1
            by_epic = await facet(page, "epic") == per_epic
            # For the word the page shows fifty and asks for the rest: the server's paging, walked
            # in small pages, gives the same list.
            await search_on_page(page, word)
            small = await asked(client, word, size=20)
            big = await asked(client, word)
            await expect(page.locator("#search-count")).to_have_text("63 results", timeout=SLOW)
            fifty = await page.locator("#search-results tr.result").count()
            more = await page.locator("#show-more").inner_text()
            same_as_server(await whole_list(page, 63), big["items"])
            # A kind keeps the server's order, says how many of how many, and is in the address.
            await page.locator("#search-facets [data-facet=type] button[data-value=task]").click()
            tasks = [i for i in big["items"] if i["item_type"] == "task"]
            await expect(page.locator("#search-count")).to_have_text(
                f"{len(tasks)} results of 63.", timeout=SLOW
            )
            kind_in_address = (await address(page))[1]["search_type"] == "task"
            same_as_server(await whole_list(page, len(tasks)), tasks)
            await page.reload()
            await page.wait_for_selector("#search-results tr.result", timeout=SLOW)
            reopened = (
                await page.locator("#search-box").input_value() == word
                and await page.locator("#search-mode").count() == 0
            )
            same_as_server(await whole_list(page, len(tasks)), tasks)
            await page.locator("#search-facets [data-facet=type] button.on").click()
            epic_number = str(number_of(epic))
            await page.locator(
                f"#search-facets [data-facet=epic] button[data-value='{epic_number}']"
            ).click()
            under = [i for i in big["items"] if epics.get(i["ref"]) == epic]
            await expect(page.locator("#search-count")).to_have_text(
                f"{len(under)} results of 63.", timeout=SLOW
            )
            same_as_server(await whole_list(page, len(under)), under)
            await page.locator(
                f"#search-results tr.result[data-ref='{story}'] a.result-title"
            ).click()
            await item_loaded(page, story)
            await expect(page.locator("#item-title")).to_contain_text(
                f"{code_of(story)} Send the invoice", timeout=SLOW
            )
            record = (await address(page))[1]
            # The way back is the Search page as it was left: the same words and the narrowing.
            await view_crumb(page).click()
            await on_page(page, "search")
            back = (await address(page))[1]
            await expect(page.locator("#search-count")).to_have_text(
                f"{len(under)} results of 63.", timeout=SLOW
            )
            assert w.problems == []
    proof(
        "M20",
        "with the embedding service back, meaning results on the Search page are what search "
        "returns in the same order with the server's scores in their reasons, the status of a "
        "confirmed and of an unconfirmed note, and the counts by kind, status and epic",
        {story, other, f"note:{decision['id']}", f"note:{note['id']}", f"rule:{rule['id']}"} <= mine
        and scored
        and statuses_hold
        and by_type
        and by_status
        and by_epic,
        f"{by_meaning['total']} results by meaning",
    )
    proof(
        "M20",
        "paging, filters and address match the server: fifty shown and 'Show 13 more' for 63 "
        "results (the server's own paging in pages of 20 gives the same list), a kind and an epic "
        "narrow it in the server's order and stay in the address, the page opens again exactly so "
        "after a reload (there is no choice of how to search: it is always for words and meaning "
        "together), and a result opens its page, from which the way back is the same search "
        "with the same narrowing",
        small["items"] == big["items"]
        and small["total"] == big["total"] == 63
        and fifty == 50
        and more == "Show 13 more"
        and kind_in_address
        and reopened
        and record == {"item": story, "from": "search"}
        and back.get("search") == word
        and back.get("search_epic") == epic_number,
        f"{len(tasks)} tasks, {len(under)} in one epic",
    )


# -- possible duplicates -------------------------------------------------------------------------


def more_twins(count: int) -> list[tuple[str, str]]:
    """Pairs that say the same thing in other words and nothing else alike."""
    pairs = []
    for _ in range(count):
        one, other = unique_word(), unique_word()
        pairs.append((f"Customer {one} {other}", f"Client {one} {other}"))
    return pairs


async def possible_duplicates() -> None:
    pairs = [*DUPLICATES, *more_twins(15)]
    async with fresh_project(URL) as (client, board, project):
        epic_a, epic_b, firsts, seconds = await twin_epics(client, pairs)
        on_server = await every_overlap(client, epics=[epic_a, epic_b])
        expected = pair_triples(on_server)
        default = on_server["threshold"]
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await w.open("#/graph")
            await page.locator("#global-search-go").click()
            await on_page(page, "search")
            await page.locator("#search-tab-dups").click()
            await expect(page.locator(".search-page")).to_have_attribute("data-mode", "duplicates")
            await no_panel_or_drawer(page)
            await expect(page.locator("#dup-min")).to_have_value(str(default), timeout=SLOW)
            await page.locator("#dup-epics").fill(f"{number_of(epic_a)}, {number_of(epic_b)}")
            await page.locator("#dup-apply").click()
            await expect(page.locator("#dup-pairs tr.pair")).to_have_count(20, timeout=SLOW)
            await expect(page.locator("#dup-count")).to_have_text(
                f"{on_server['total']} pairs at {default} or more alike; 20 shown"
            )
            shown = await pairs_on(page)
            descending = [round(s, 6) for _, _, s in shown] == sorted(
                [round(s, 6) for _, _, s in shown], reverse=True
            )
            small: list[tuple[str, str, float]] = []
            offset: int | None = 0
            while offset is not None:
                page_of = await ok(
                    client, "find_overlaps", epics=[epic_a, epic_b], limit=7, offset=offset
                )
                small.extend(pair_triples(page_of))
                offset = page_of["next_offset"]
            ours = {(a, b) for a, b, _ in shown if {a, b} <= {*firsts, *seconds}}
            sample = page.locator(f"tr.pair[data-first='{firsts[0]}'][data-second='{seconds[0]}']")
            await expect(sample.locator("td.side")).to_have_count(2)
            score = float(await sample.locator(".score strong").inner_text())
            sample_score = next(s for a, b, s in expected if (a, b) == (firsts[0], seconds[0]))
            strict = await every_overlap(client, epics=[epic_a, epic_b], min_similarity=0.99)
            await page.locator("#dup-min").fill("0.99")
            await page.locator("#dup-apply").click()
            await expect(page.locator("#dup-count")).to_contain_text(
                "at 0.99 or more", timeout=SLOW
            )
            strict_shown = await pairs_on(page)
            one_epic = await every_overlap(client, epics=[epic_b], min_similarity=0.99)
            await page.locator("#dup-epics").fill(str(number_of(epic_b)))
            await redrawn(page, "#dup-pairs", page.locator("#dup-apply").click())
            narrowed = await pairs_on(page)
            await redrawn(page, "#dup-pairs", page.locator("#dup-reset").click())
            await expect(page.locator("#dup-min")).to_have_value(str(default), timeout=SLOW)
            assert w.problems == []
    proof(
        "M20",
        "Possible duplicates, the second tab of the Search page, lists the pairs find_overlaps "
        "gives in the same order with the same scores (twenty, then more on request), the "
        "server's own paging in pages of seven gives the same list, a higher threshold and a "
        "narrower choice of epics keep exactly the pairs MCP keeps, and starting again goes back "
        "to the server's threshold",
        shown == expected == small
        and descending
        and len(expected) > 20
        and ours == set(zip(firsts, seconds, strict=True))
        and abs(score - sample_score) < 0.001
        and strict_shown == pair_triples(strict)
        and 0 < len(strict["items"]) < len(expected)
        and narrowed == pair_triples(one_epic),
        f"{len(expected)} pairs at {default}, {len(strict['items'])} at 0.99",
    )


async def marking_links() -> None:
    pair = DUPLICATES[1]
    async with fresh_project(URL) as (client, board, project):
        epic_a, epic_b, (first,), (second,) = await twin_epics(client, [pair])
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await w.open(f"#/search?dups=1&dup_epics={number_of(epic_a)}")
            shown = await pairs_on(page)
            row = page.locator(f"tr.pair[data-first='{first}'][data-second='{second}']")
            before = {
                "links": await link_count(client),
                "at": await links_of(client, first, second),
                "latest": (await ok(client, "changes_since", after_event_id=0, limit=1))[
                    "latest_event_id"
                ],
            }
            # Looking, changing the threshold and the epics, and searching, change nothing.
            await page.locator("#dup-min").fill("0.6")
            await page.locator("#dup-apply").click()
            await expect(page.locator("#dup-count")).to_contain_text("at 0.6 or more", timeout=SLOW)
            await pairs_on(page)
            await page.locator("#search-tab-results").click()
            await search_on_page(page, pair[0])
            await expect(page.locator("#search-results tr.result").first).to_be_visible(
                timeout=SLOW
            )
            await page.locator("#search-tab-dups").click()
            await pairs_on(page)
            await page.wait_for_timeout(1500)
            untouched = (
                await link_count(client) == before["links"]
                and await links_of(client, first, second) == before["at"]
                and (await ok(client, "changes_since", after_event_id=0, limit=1))[
                    "latest_event_id"
                ]
                == before["latest"]
            )
            names = f"{code_of(first)} and {code_of(second)}"
            await row.get_by_role("button", name=f"Mark {names} as duplicates").click()
            await expect(
                row.get_by_role("button", name=f"{names} are marked as duplicates")
            ).to_be_disabled(timeout=SLOW)
            one = await links_of(client, first, second)
            one_count = await link_count(client)
            link = (await ok(client, "list_links", node=first))["items"][0]
            await expect(row).to_contain_text("Already linked: duplicates")
            await row.get_by_role("button", name=f"Mark {names} as related").click()
            await expect(
                row.get_by_role("button", name=f"{names} are marked as related")
            ).to_be_disabled(timeout=SLOW)
            both = await links_of(client, first)
            final = await link_count(client)
            assert w.problems == []
    proof(
        "M20",
        "on the Search page's Possible duplicates tab, searching and looking at pairs create no "
        "link and no event, and marking a pair as duplicates creates exactly one link of that "
        "kind, shown as already linked, and marking it as related exactly one more",
        (first, second) in {(a, b) for a, b, _ in shown}
        and untouched
        and one
        == {
            first: [(first, second, "duplicates")],
            second: [(first, second, "duplicates")],
        }
        and one_count == before["links"] + 1
        and link["link_type"] == "duplicates"
        and both[first] == [(first, second, "duplicates"), (first, second, "relates_to")]
        and final == before["links"] + 2,
        f"{code_of(first)} and {code_of(second)}",
    )


# -- Markdown that is only text, references and backlinks, the quick switcher --------------


def card(page: Page, ref: str) -> Any:
    """The article that draws an epic, story or task on its page."""
    return detail(page, ref)


async def markdown_is_text() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Markdown {word}")
        story = await made(client, "story", f"Written {word}", epic, body=BODY.format(word=word))
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await open_item(w, story)
            text = card(page, story).locator(".body-text .markdown")
            await expect(text.locator("pre code")).to_have_text(f"code {word}", timeout=SLOW)
            shown = await text.inner_text()
            await page.wait_for_load_state("networkidle")
            foreign = [r for r in w.requests if not r.startswith(w.origin)]
            docs = text.get_by_role("link", name="the docs")
            proof(
                "M13",
                "on the page of a story, a body with a heading, a list, code, a script tag and an "
                "image tag from another address shows the structure and the two tags as literal "
                "text, a web address is a link that opens in a new tab, and the browser makes no "
                "request to any other address",
                await text.locator("h4").inner_text() == f"A heading {word}"
                and await text.locator("ul > li").count() == 2
                and "<script>window.hacked = 1</script>" in shown
                and '<img src="http://other.invalid/p.png"' in shown
                and await text.locator("script, img, iframe, object, embed").count() == 0
                and await page.evaluate("window.hacked === undefined")
                and await docs.get_attribute("target") == "_blank"
                and "noopener" in (await docs.get_attribute("rel") or "")
                and foreign == []
                and w.dialogs == [],
                f"{len(w.requests)} requests, none elsewhere",
            )


async def references() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"References {word}")
        target = await made(client, "story", f"Target {word}", epic)
        rule = await ok(client, "create_rule", actor="a", title=f"Ruled {word}", node=target)
        decided = await make_note(client, f"Decided {word}", "Because", decided_by="Marie")
        known = await make_note(client, f"Known {word}")
        missing = "story:987654321"
        # Only epics, stories, tasks, requirements and notes are references: [[decision:N]],
        # [[fact:N]], [[belief:N]] and [[node:N]] are not, and each stays the text it is.
        body = (
            f"See [[{target}]], [[rule:{rule['id']}]], [[note:{decided['id']}]], "
            f"[[note:{known['id']}]], [[decision:{decided['id']}]], [[fact:{known['id']}]], "
            f"[[belief:3]], [[node:{number_of(target)}]] and [[{missing}]]."
        )
        mentioner = await made(client, "story", f"Mentioner {word}", epic, body=body)
        before = await links_of(client, target, mentioner)
        status_before = await ok(client, "get_node", node=mentioner)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            here = item_address(mentioner, "board")
            await open_item(w, mentioner)
            text = card(page, mentioner).locator(".body-text .markdown")
            await expect(text.locator(".reference")).to_have_count(5, timeout=SLOW)

            def reference(ref: str) -> Any:
                return text.locator(f".reference[data-ref='{ref}']")

            await expect(reference(target)).to_contain_text(f"{code_of(target)} Target {word}")
            await expect(reference(f"note:{known['id']}")).to_contain_text(f"Known {word}")
            gone = reference(missing)
            missing_text = await gone.inner_text()
            missing_link = await gone.get_attribute("href")
            shown = await text.inner_text()
            old_kind = (
                "[[belief:3]]" in shown
                and f"[[node:{number_of(target)}]]" in shown
                and f"[[decision:{decided['id']}]]" in shown
                and f"[[fact:{known['id']}]]" in shown
                and await text.locator(".reference[data-kind=belief]").count() == 0
                and await text.locator(".reference[data-kind=node]").count() == 0
                and await text.locator(".reference[data-kind=decision]").count() == 0
                and await text.locator(".reference[data-kind=fact]").count() == 0
            )
            await ok(
                client, "update_node", actor="a", node=target, expected_version=1,
                title=f"Renamed {word}",
            )  # fmt: skip
            await expect(reference(target)).to_contain_text(f"Renamed {word}", timeout=SLOW)
            await reference(target).click()
            await item_loaded(page, target)
            await expect(page.locator("#item-title")).to_contain_text(
                f"Renamed {word}", timeout=SLOW
            )
            followed = (await hash_params(page)) == {"item": target, "from": "board"}
            await w.go(here)
            await item_loaded(page, mentioner)
            decision_ref = f"note:{decided['id']}"
            await reference(decision_ref).click()
            await item_loaded(page, decision_ref)
            await no_panel_or_drawer(page)
            path = await crumbs(page)
            by_page = path[2] == "Board" and path[-1] == f"N-{decided['id']}"
            same_links = await links_of(client, target, mentioner) == before
            same_status = (await ok(client, "get_node", node=mentioner))["version"] == (
                status_before["version"]
            )
            # "Mentioned in" on the target is find_mentions.
            await w.go(item_address(target, "board"))
            await item_loaded(page, target)
            section = item_page(page).locator("section[data-part=mentions]")
            mentions = await ok(client, "find_mentions", node=target)
            await expect(section.locator("li.mention")).to_have_count(
                len(mentions["items"]), timeout=SLOW
            )
            listed = await section.locator("li.mention").evaluate_all(
                "els => els.map(e => e.dataset.source)"
            )
            expected = [m["source"]["ref"] for m in mentions["items"]]
            assert w.problems == []
        proof(
            "M13",
            "[[epic:N]], [[story:N]], [[task:N]], [[rule:N]] and [[note:N]] show "
            "as links with the item's current title and follow a rename, a missing number is "
            "plain text marked missing, [[decision:N]], [[fact:N]], [[belief:N]] and the old "
            "[[node:N]] are only the words "
            "they are, following one opens that record's own page (still in the Board, as its "
            "breadcrumb says), and writing references makes no link and changes no status",
            missing_text == f"[[{missing}]] (missing)"
            and missing_link is None
            and old_kind
            and followed
            and by_page
            and same_links
            and same_status,
            f'"{missing_text}"',
        )
        proof(
            "M13",
            "'Mentioned in' on the page of a story equals find_mentions through MCP",
            sorted(listed) == sorted(expected) and mentioner in listed,
            ", ".join(listed),
        )


async def switching() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Switching {word}")
        story = await made(client, "story", f"Findable {word}", epic)
        note = await make_note(client, f"Switchable note {word}")
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            opened = []
            for name in PAGES:
                await w.open(f"#/{name}")
                await page.wait_for_selector("#page", timeout=SLOW)
                await page.keyboard.press("Control+k")
                await expect(page.locator("#switcher")).to_be_visible(timeout=SLOW)
                opened.append(
                    await page.locator("#switcher-input").evaluate(
                        "e => e === document.activeElement"
                    )
                )
                await page.keyboard.press("Escape")
                await expect(page.locator("#switcher")).to_have_count(0)
            await w.go("#/roadmap")
            await page.keyboard.press("Control+k")
            await page.keyboard.type(f"findable {word}")
            await expect(page.locator("#switcher [role=option]").first).to_have_attribute(
                "data-ref", story, timeout=SLOW
            )
            await page.keyboard.press("Enter")
            await item_loaded(page, story)
            await expect(page.locator("#item-title")).to_contain_text(
                f"Findable {word}", timeout=SLOW
            )
            by_word = (
                await hash_params(page) == {"item": story, "from": "roadmap"}
                and (await crumbs(page))[2] == "Roadmap"
                and await sidebar_current(page) == ["roadmap"]
            )
            # By the code a person reads for it (S-1), which is its kind and number.
            await w.go("#/board")
            await page.keyboard.press("Control+k")
            await page.keyboard.type(code_of(epic))
            await expect(page.locator("#switcher [role=option]").first).to_have_attribute(
                "data-ref", epic, timeout=SLOW
            )
            await page.keyboard.press("Enter")
            await item_loaded(page, epic)
            await expect(page.locator("#item-title")).to_contain_text(
                f"{code_of(epic)} Switching {word}"
            )
            by_code = await hash_params(page) == {"item": epic, "from": "board"}
            await w.go("#/graph?tab=around")
            await page.keyboard.press("Control+k")
            await page.keyboard.type(f"note {note['id']}")
            await expect(page.locator("#switcher [role=option]")).to_have_count(1, timeout=SLOW)
            await page.keyboard.press("Enter")
            await item_loaded(page, f"note:{note['id']}")
            by_kind = (
                await hash_params(page) == {"item": f"note:{note['id']}", "from": "graph"}
                and (await crumbs(page))[2] == "Graph"
            )
            await page.keyboard.press("Control+k")
            await page.keyboard.type("activity")
            await page.keyboard.press("Enter")
            await on_page(page, "activity")
            await no_panel_or_drawer(page)
            assert w.problems == []
    proof(
        "M20",
        "the quick switcher opens from the keyboard on every page with its box ready, jumps to "
        "an item by a word of its title or by its code (E-1, S-1, T-1), to a note by its kind and "
        "number (each on that record's own page, in the view the person was on: its breadcrumb "
        "and the sidebar say so), and to a page such as Activity by name",
        all(opened) and len(opened) == len(PAGES) and by_word and by_code and by_kind,
        f"{len(PAGES)} pages",
    )
