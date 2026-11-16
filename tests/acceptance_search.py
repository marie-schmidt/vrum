"""Chapter 11 of the acceptance scenario: finding and connecting on the board (M13 and M16),
driven in a real headless browser against the real containers, with what the page shows compared
with what a real MCP client gets from the same tools. Search is a drawer that opens over Board,
Graph and Roadmap from the box in the top bar; a result opens the ordinary detail panel beside it.
Possible duplicates is a mode of the same drawer. It is searched by exact words with the embedding
service stopped, and by meaning with it back (scores, labels, counts and paging equal to
`search`), the pairs and scores equal to `find_overlaps`, and what a click on a pair creates
(exactly one link) compared with what a search creates (none). Markdown that is only ever text,
references and their backlinks, and the quick switcher follow. There is no Ask: Vrum has no
language model."""

import re
from typing import Any

from playwright.async_api import Error as BrowserError
from playwright.async_api import Page, expect

from tests.acceptance_board import same_words
from tests.acceptance_scenario import URL, Failed, chapter, clip, proof
from tests.board_context import fresh_project, hash_params, panel
from tests.board_helpers import PAGES, SLOW, browser_page, made
from tests.board_search import (
    DUPLICATES,
    VIEWS,
    address,
    asked,
    drawer,
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
    search_in_drawer,
    shown_triples,
    triples,
    twin_epics,
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
    unique_word,
)
from tests.test_board_references_integration import BODY
from tests.test_board_search_integration import RECORD, same_as_server

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
        rule = await ok(client, "create_rule", actor="a", title=f"Rule {word}", story_id=story)
        decision = await make_decision(client, f"Decision {word}", node_ids=[story])
        fact = await make_fact(client, f"A fact {word}", node_ids=[story])
        await confirm(client, "fact", fact)
        await reindexed(client)
        on_server = await asked(client, word, "words")
        wanted = {
            ("epic", epic),
            ("story", story),
            ("task", task),
            ("rule", rule["id"]),
            ("decision", decision["id"]),
            ("fact", fact["id"]),
        }
        before = (await ok(client, "server_status"))["counts"]
        head = (await ok(client, "changes_since", after_event_id=0, limit=1))["latest_event_id"]
        opened = []
        async with browser_page(board, token=project.key) as w:
            page = w.page
            with embeddings_stopped():
                late = await made(client, "story", f"Made while the service was down {later}", epic)
                for view in VIEWS:
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
                    await expect(page.locator("#search-waiting")).to_contain_text(
                        "1 item waiting to be indexed"
                    )
                    paraphrase = "Clients get a bill by mail"
                    await search_in_drawer(page, paraphrase, "meaning")
                    await expect(page.locator("#search-problem")).to_contain_text(
                        "Searching by meaning is not possible now", timeout=SLOW
                    )
                    said = await refused(client, "search", query=paraphrase, mode="meaning")
                    assert same_words(await page.locator("#search-problem").inner_text(), said)
                    await search_in_drawer(page, word, "words")
                    rows = await result_rows(page, 6)
                    same_as_server(rows, on_server["items"])
                    for row in rows:
                        result = (
                            f"#search-results li.result[data-type={row['type']}]"
                            f"[data-id='{row['id']}']"
                        )
                        await page.locator(f"{result} a.result-title").click()
                        record = f"{RECORD[row['type']]}:{row['id']}"
                        await expect(page).to_have_url(re.compile(rf"sel={record}(&|$)"))
                        if RECORD[row["type"]] == "node":
                            await expect(page.locator("#panel #card-title")).to_contain_text(
                                row["title"], timeout=SLOW
                            )
                        else:
                            await expect(
                                page.locator(f"#panel .record-detail[data-record='{record}']")
                            ).to_be_visible(timeout=SLOW)
                        assert len(await result_rows(page, 6)) == 6
                    opened.append(view)
                    await page.locator("#drawer-close").click()
                    await expect(page.locator("#drawer")).to_be_hidden()
            await reindexed(client)
            after = (await ok(client, "server_status"))["counts"]
            news = (await ok(client, "changes_since", after_event_id=head, limit=200))[
                "latest_event_id"
            ]
            assert w.problems == []
    proof(
        "M16",
        "from each of Board, Graph and Roadmap, with the embedding service stopped, the search "
        "box finds by exact words what search finds, labelled exact words, a story made while "
        "the service was down at once (one item waiting), says in the server's words that meaning "
        "is not possible now, and opens a result of every kind in the ordinary detail panel with "
        "the drawer still open; merely searching writes nothing",
        {(r["item_type"], r["id"]) for r in on_server["items"]} == wanted
        and opened == list(VIEWS)
        and after == {**before, "stories": before["stories"] + 1, "events": before["events"] + 1}
        and news == head + 1,
        f"{len(opened)} views, six kinds of result each",
    )


# -- meaning, with the scores, the labels, the counts and the paging -------------------------


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
            story_id=story,
        )
        decision = await make_decision(
            client, f"Invoices go to the customer by email {word}", node_ids=[story]
        )
        await confirm(client, "decision", decision)
        fact = await make_fact(
            client, f"Customers get their invoice by email {word}", node_ids=[task]
        )
        for number in range(55):
            await made(client, "task", f"Filler {number} {word}", story)
        await reindexed(client)
        epics = await epic_of_each(client)
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await w.open("#/roadmap")
            await page.locator("#global-search-go").click()
            await expect(drawer(page, "search")).to_be_visible(timeout=SLOW)
            await expect(page.locator("#search-waiting")).to_contain_text("0 items waiting")
            await search_in_drawer(page, paraphrase, "meaning")
            by_meaning = await asked(client, paraphrase, "meaning")
            mine = {(i["item_type"], i["id"]) for i in by_meaning["items"]}
            rows = await whole_list(page, by_meaning["total"])
            same_as_server(rows, by_meaning["items"])
            by_id = {(row["type"], row["id"]): row for row in rows}
            labels_hold = by_id[("decision", decision["id"])]["label"].startswith(
                "Decision, confirmed by Marie"
            ) and by_id[("fact", fact["id"])]["label"].startswith("Fact, not confirmed by a person")
            scored = all("similar in meaning (score " in row["why"] for row in rows)
            by_type = await facet(page, "type") == by_meaning["counts_by_type"]
            by_status = await facet(page, "status") == by_meaning["counts_by_status"]
            per_epic: dict[str, int] = {}
            for item in by_meaning["items"]:
                found = epics.get((item["item_type"], item["id"]))
                if found is not None:
                    per_epic[str(found)] = per_epic.get(str(found), 0) + 1
            by_epic = await facet(page, "epic") == per_epic
            # By words the drawer shows fifty and asks for the rest: the server's paging, walked
            # in small pages, gives the same list.
            await search_in_drawer(page, word, "words")
            small = await asked(client, word, "words", size=20)
            big = await asked(client, word, "words")
            await expect(page.locator("#search-count")).to_have_text("63 results.", timeout=SLOW)
            fifty = await page.locator("#search-results li.result").count()
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
            await page.wait_for_selector("#search-results li.result", timeout=SLOW)
            reopened = (
                await page.locator("#search-box").input_value() == word
                and await page.locator("#search-mode").input_value() == "words"
            )
            same_as_server(await whole_list(page, len(tasks)), tasks)
            await page.locator("#search-facets [data-facet=type] button.on").click()
            await page.locator(
                f"#search-facets [data-facet=epic] button[data-value='{epic}']"
            ).click()
            under = [i for i in big["items"] if epics.get((i["item_type"], i["id"])) == epic]
            await expect(page.locator("#search-count")).to_have_text(
                f"{len(under)} results of 63.", timeout=SLOW
            )
            same_as_server(await whole_list(page, len(under)), under)
            await page.locator(
                f"#search-results li.result[data-type=story][data-id='{story}'] a.result-title"
            ).click()
            await expect(page.locator("#panel #card-title")).to_contain_text(
                f"#{story} Send the invoice", timeout=SLOW
            )
            choices = (await address(page))[1]
            assert w.problems == []
    proof(
        "M16",
        "with the embedding service back, meaning results in the drawer are what search returns "
        "in the same order with the server's scores in their reasons, the labels of a decision "
        "and a fact (confirmed or not), and the counts by kind, status and epic",
        {
            ("story", story),
            ("story", other),
            ("decision", decision["id"]),
            ("fact", fact["id"]),
            ("rule", rule["id"]),
        }
        <= mine
        and scored
        and labels_hold
        and by_type
        and by_status
        and by_epic,
        f"{by_meaning['total']} results by meaning",
    )
    proof(
        "M16",
        "paging, filters and address match the server: fifty shown and 'Show 13 more' for 63 "
        "results (the server's own paging in pages of 20 gives the same list), a kind and an epic "
        "narrow it in the server's order and stay in the address, the page opens again exactly so "
        "after a reload, and a result keeps the choices",
        small["items"] == big["items"]
        and small["total"] == big["total"] == 63
        and fifty == 50
        and more == "Show 13 more"
        and kind_in_address
        and reopened
        and choices["search_epic"] == str(epic)
        and choices["sel"] == f"node:{story}",
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
        on_server = await every_overlap(client, epic_ids=[epic_a, epic_b])
        expected = pair_triples(on_server)
        default = on_server["threshold"]
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await w.open("#/graph")
            await page.locator("#global-search-go").click()
            await expect(drawer(page, "search")).to_be_visible(timeout=SLOW)
            await page.locator("#search-tab-dups").click()
            await expect(page.locator("#drawer")).to_have_attribute("data-mode", "duplicates")
            await expect(page.locator("#dup-min")).to_have_value(str(default), timeout=SLOW)
            await page.locator("#dup-epics").fill(f"{epic_a}, {epic_b}")
            await page.locator("#dup-apply").click()
            await expect(page.locator("#dup-pairs li.pair")).to_have_count(20, timeout=SLOW)
            await expect(page.locator("#dup-count")).to_have_text(
                f"{on_server['total']} pairs at {default} or more alike; 20 shown."
            )
            shown = await pairs_on(page)
            descending = [round(s, 6) for _, _, s in shown] == sorted(
                [round(s, 6) for _, _, s in shown], reverse=True
            )
            small: list[tuple[int, int, float]] = []
            offset: int | None = 0
            while offset is not None:
                page_of = await ok(
                    client, "find_overlaps", epic_ids=[epic_a, epic_b], limit=7, offset=offset
                )
                small.extend(pair_triples(page_of))
                offset = page_of["next_offset"]
            ours = {(a, b) for a, b, _ in shown if {a, b} <= {*firsts, *seconds}}
            sample = page.locator(f"li.pair[data-first='{firsts[0]}'][data-second='{seconds[0]}']")
            await expect(sample.locator(".side")).to_have_count(2)
            score = float(await sample.locator(".score strong").inner_text())
            sample_score = next(s for a, b, s in expected if (a, b) == (firsts[0], seconds[0]))
            strict = await every_overlap(client, epic_ids=[epic_a, epic_b], min_similarity=0.99)
            await page.locator("#dup-min").fill("0.99")
            await page.locator("#dup-apply").click()
            await expect(page.locator("#dup-count")).to_contain_text(
                "at 0.99 or more", timeout=SLOW
            )
            strict_shown = await pairs_on(page)
            one_epic = await every_overlap(client, epic_ids=[epic_b], min_similarity=0.99)
            await page.locator("#dup-epics").fill(str(epic_b))
            await redrawn(page, "#dup-pairs", page.locator("#dup-apply").click())
            narrowed = await pairs_on(page)
            await redrawn(page, "#dup-pairs", page.locator("#dup-reset").click())
            await expect(page.locator("#dup-min")).to_have_value(str(default), timeout=SLOW)
            assert w.problems == []
    proof(
        "M16",
        "Possible duplicates, a mode of the search drawer, lists the pairs find_overlaps gives in "
        "the same order with the same scores (twenty, then more on request), the server's own "
        "paging in pages of seven gives the same list, a higher threshold and a narrower choice "
        "of epics keep exactly the pairs MCP keeps, and starting again goes back to the "
        "server's threshold",
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
            await w.open(f"#/roadmap?drawer=search&dups=1&dup_epics={epic_a}")
            shown = await pairs_on(page)
            row = page.locator(f"li.pair[data-first='{first}'][data-second='{second}']")
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
            await search_in_drawer(page, pair[0], "both")
            await expect(page.locator("#search-results li.result").first).to_be_visible(
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
            await row.get_by_role("button", name="Mark as duplicates").click()
            await expect(
                row.get_by_role("button", name="Already marked as duplicates")
            ).to_be_disabled(timeout=SLOW)
            one = await links_of(client, first, second)
            one_count = await link_count(client)
            link = (await ok(client, "list_links", node_id=first))["items"][0]
            await expect(row).to_contain_text("already linked: duplicates")
            await row.get_by_role("button", name="Mark as related").click()
            await expect(
                row.get_by_role("button", name="Already marked as related")
            ).to_be_disabled(timeout=SLOW)
            both = await links_of(client, first)
            final = await link_count(client)
            assert w.problems == []
    proof(
        "M16",
        "searching and looking at pairs create no link and no event, and marking a pair as "
        "duplicates creates exactly one link of that kind, shown as already linked, and marking "
        "it as related exactly one more",
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
        f"nodes {first} and {second}",
    )


# -- Markdown that is only text, references and backlinks, the quick switcher --------------


def card(page: Page) -> Any:
    return panel(page).locator(".card-detail")


async def markdown_is_text() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Markdown {word}")
        story = await made(client, "story", f"Written {word}", epic, body=BODY.format(word=word))
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await w.open(f"#/board?epic={epic}&sel=node:{story}")
            text = card(page).locator(".body-text .markdown")
            await expect(text.locator("pre code")).to_have_text(f"code {word}", timeout=SLOW)
            shown = await text.inner_text()
            await page.wait_for_load_state("networkidle")
            foreign = [r for r in w.requests if not r.startswith(w.origin)]
            docs = text.get_by_role("link", name="the docs")
            proof(
                "M13",
                "a body with a heading, a list, code, a script tag and an image tag from another "
                "address shows the structure and the two tags as literal text, a web address is a "
                "link that opens in a new tab, and the browser makes no request to any other "
                "address",
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
        rule = await ok(client, "create_rule", actor="a", title=f"Ruled {word}", story_id=target)
        decision = await make_decision(client, f"Decided {word}")
        fact = await make_fact(client, f"Known {word}")
        missing = 987654321
        body = (
            f"See [[node:{target}]], [[rule:{rule['id']}]], [[decision:{decision['id']}]], "
            f"[[fact:{fact['id']}]], [[belief:3]] and [[node:{missing}]]."
        )
        mentioner = await made(client, "story", f"Mentioner {word}", epic, body=body)
        before = await links_of(client, target, mentioner)
        status_before = await ok(client, "get_node", node_id=mentioner)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await w.open(f"#/board?epic={epic}&sel=node:{mentioner}")
            text = card(page).locator(".body-text .markdown")
            await expect(text.locator(".reference")).to_have_count(5, timeout=SLOW)

            def reference(kind: str, ident: int) -> Any:
                return text.locator(f".reference[data-kind={kind}][data-id='{ident}']")

            await expect(reference("node", target)).to_contain_text(f"#{target} Target {word}")
            await expect(reference("fact", fact["id"])).to_contain_text(f"Known {word}")
            gone = reference("node", missing)
            missing_text = await gone.inner_text()
            missing_link = await gone.get_attribute("href")
            old_kind = (
                "[[belief:3]]" in await text.inner_text()
                and await text.locator(".reference[data-kind=belief]").count() == 0
            )
            await ok(
                client, "update_node", actor="a", node_id=target, expected_version=1,
                title=f"Renamed {word}",
            )  # fmt: skip
            await expect(reference("node", target)).to_contain_text(f"Renamed {word}", timeout=SLOW)
            await reference("node", target).click()
            await expect(card(page).locator("h2").first).to_contain_text(
                f"Renamed {word}", timeout=SLOW
            )
            followed = (await hash_params(page))["sel"] == f"node:{target}"
            await w.go(f"#/board?epic={epic}&sel=node:{mentioner}")
            await reference("decision", decision["id"]).click()
            await expect(
                panel(page).locator(f".record-detail[data-record='decision:{decision['id']}']")
            ).to_be_visible(timeout=SLOW)
            same_links = await links_of(client, target, mentioner) == before
            same_status = (await ok(client, "get_node", node_id=mentioner))["version"] == (
                status_before["version"]
            )
            # "Mentioned in" on the target is find_mentions.
            await w.go(f"#/board?epic={epic}&sel=node:{target}")
            section = panel(page).locator("section[data-part=mentions]")
            mentions = await ok(client, "find_mentions", node_id=target)
            await expect(section.locator("li.mention")).to_have_count(
                len(mentions["items"]), timeout=SLOW
            )
            listed = await section.locator("li.mention").evaluate_all(
                "els => els.map(e => e.dataset.source)"
            )
            expected = [
                f"{m['source']['item_type']}:{m['source']['id']}" for m in mentions["items"]
            ]
            assert w.problems == []
        proof(
            "M13",
            "[[node:N]], [[rule:N]], [[decision:N]] and [[fact:N]] show as links with the item's "
            "current title and follow a rename, a missing id is plain text marked missing, "
            "[[belief:N]] is only the words it is, following one opens the ordinary panel, and "
            "writing references makes no link and changes no status",
            missing_text == f"[[node:{missing}]] (missing)"
            and missing_link is None
            and old_kind
            and followed
            and same_links
            and same_status,
            f'"{missing_text}"',
        )
        proof(
            "M13",
            "'Mentioned in' on the panel equals find_mentions through MCP",
            sorted(listed) == sorted(expected) and f"story:{mentioner}" in listed,
            ", ".join(listed),
        )


async def switching() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Switching {word}")
        story = await made(client, "story", f"Findable {word}", epic)
        fact = await make_fact(client, f"Switchable fact {word}")
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
                "data-ref", f"node:{story}", timeout=SLOW
            )
            await page.keyboard.press("Enter")
            await expect(card(page)).to_contain_text(f"Findable {word}", timeout=SLOW)
            by_word = (await hash_params(page))["sel"] == f"node:{story}" and "#/roadmap" in (
                await page.evaluate("location.hash")
            )
            await w.go("#/graph?view=context&as=list")
            await page.keyboard.press("Control+k")
            await page.keyboard.type(f"fact {fact['id']}")
            await expect(page.locator("#switcher [role=option]")).to_have_count(1, timeout=SLOW)
            await page.keyboard.press("Enter")
            await expect(
                panel(page).locator(f"[data-record='fact:{fact['id']}']").first
            ).to_be_visible(timeout=SLOW)
            by_kind = (await hash_params(page))["sel"] == f"fact:{fact['id']}"
            await page.keyboard.press("Control+k")
            await page.keyboard.type("activity")
            await page.keyboard.press("Enter")
            await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible(timeout=SLOW)
            assert w.problems == []
    proof(
        "M13",
        "the quick switcher opens from the keyboard on every page with its box ready, jumps to "
        "an item by a word of its title and to a fact by its kind and id (in the panel of the "
        "view the person is on), and opens a drawer by name",
        all(opened) and len(opened) == len(PAGES) and by_word and by_kind,
        f"{len(PAGES)} pages",
    )
