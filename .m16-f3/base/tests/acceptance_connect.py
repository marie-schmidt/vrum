"""Chapter 12 of the acceptance scenario: finding and connecting on the board (M13), driven in a
real headless browser against the real containers, with what the page shows compared with what a
real MCP client gets from the same tools. Search by words and by meaning (with the embedding
service stopped and back), the counts, the overlaps and what a click on a pair creates, Markdown
that is only ever text, references and their backlinks, the quick switcher, and Ask with the
scripted language model of the stack."""

import re
from typing import Any

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import Locator, Page, expect

from tests.acceptance_finding import DUPLICATES
from tests.acceptance_scenario import NO_MODEL_URL, URL, Failed, chapter, clip, proof
from tests.board_helpers import (
    PAGES,
    SLOW,
    asked,
    board_url,
    browser_page,
    every_overlap,
    facet,
    links_of,
    listed,
    made,
    pairs_on,
    search_for,
    twin_epics,
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

ADA = "Ada Lovelace"


def same_words(board_words: str, mcp_words: str) -> bool:
    """The board said what the tool says, without the protocol's "Error executing tool"."""
    return bool(board_words.strip()) and mcp_words.endswith(board_words.strip())


def card(page: Page) -> Locator:
    return page.locator("#panel .card-detail")


async def connecting() -> None:
    chapter("Finding and connecting on the board: search, overlaps, Markdown, references, Ask")
    page_url = board_url(URL)
    try:
        async with connect(URL, the_token()) as client:
            await searching(client, page_url)
            await counting(client, page_url)
            await overlapping(client, page_url)
            await markdown(client, page_url)
            await referring(client, page_url)
            await jumping(client, page_url)
            await asking(client, page_url)
    except BrowserError as error:
        raise Failed(
            f"M13: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- 1 --------------------------------------------------------------------------------------------


async def searching(client: Client, page_url: str) -> None:
    word, later = unique_word(), unique_word()
    epic = await made(client, "epic", f"Billing {word}")
    story = await made(client, "story", f"Send the invoice to the customer by email {word}", epic)
    await reindexed(client)
    paraphrase = "Clients get a bill by mail"
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/search")
        with embeddings_stopped():
            await w.go(f"#/board?epic={epic}&card={story}")
            await page.locator("#edit-body").fill(f"The phrase {later} appears here")
            await page.get_by_role("button", name="Save changes").click()
            await expect(card(page)).to_contain_text(f"The phrase {later}", timeout=SLOW)
            await w.go("#/search")
            await expect(page.locator("#waiting")).to_contain_text("1 item waiting", timeout=SLOW)
            await search_for(page, later, "words")
            on_page = await listed(page, 1)
            by_words = await asked(client, later, "words")
            label = await page.locator("li.result .chip.matched").inner_text()
            proof(
                "M13",
                "with the embedding service stopped, a phrase added to a story on the board is "
                "found by the Search page by words at once, labelled an exact-words match, and "
                "the page says one item waits to be indexed",
                on_page == by_words == [("story", story, "words")] and label == "Exact words",
                f'"{label}", 1 item waiting',
            )
            await search_for(page, paraphrase, "meaning")
            await expect(page.locator("#search-problem")).not_to_have_text("", timeout=SLOW)
            problem = await page.locator("#search-problem").inner_text()
            message = await refused(client, "search", query=paraphrase, mode="meaning")
            proof(
                "M13",
                "searching by meaning with the service stopped says so on the page in the "
                "server's own words, and lists nothing",
                same_words(problem, message) and await page.locator("li.result").count() == 0,
                f'"{clip(problem)}"',
            )
        await reindexed(client)
        await expect(page.locator("#waiting")).to_contain_text("0 items waiting", timeout=SLOW)
        await search_for(page, paraphrase, "meaning")
        by_meaning = await asked(client, paraphrase, "meaning")
        shown = await listed(page, len(by_meaning))
        proof(
            "M13",
            "once the service is back and the count is zero, a paraphrase is found by meaning, "
            "and the page's results equal MCP search: the same ids, order and labels",
            ("story", story, "meaning") in shown and shown == by_meaning[: len(shown)],
            f"{len(shown)} results",
        )


# -- 2 --------------------------------------------------------------------------------------------


async def counting(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Counted {word}")
    other = await made(client, "epic", f"Elsewhere {word}")
    story = await made(client, "story", f"First {word}", epic)
    await made(client, "story", f"Second {word}", other)
    tasks = [await made(client, "task", f"Task {n} {word}", story) for n in range(3)]
    await ok(client, "update_node", actor="a", node_id=tasks[0], expected_version=1, status="done")
    await ok(
        client, "update_node", actor="a", node_id=tasks[1], expected_version=1, status="blocked"
    )
    await ok(client, "create_rule", actor="a", title=f"Rule {word}", story_id=story)
    await make_decision(client, f"Decision {word}")
    fact = await make_fact(client, f"A fact about {word}")
    await make_belief(client, [fact], f"A belief about {word}")
    server = await ok(client, "search", query=word, mode="words", limit=200)
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/search?q={word}&mode=words")
        shown = await listed(page, 11)
        kinds, statuses = await facet(page, "type"), await facet(page, "status")
        in_nodes = {}
        for kind in ("epic", "story", "task"):
            found = await ok(client, "list_nodes", query=word, kind=kind, limit=200)
            in_nodes[kind.title()] = len(found["items"])
        by_status = {}
        for status in ("done", "blocked"):
            found = await ok(client, "list_nodes", query=word, status=status, limit=200)
            by_status[status.title()] = len(found["items"])
        proof(
            "M13",
            "the counts by kind and by status on the Search page equal the server's counts and "
            "what the list tools return",
            {k.lower(): v for k, v in kinds.items()} == server["counts_by_type"]
            and sorted(statuses.values()) == sorted(server["counts_by_status"].values())
            and all(kinds[k] == n for k, n in in_nodes.items())
            and all(statuses[k] == n for k, n in by_status.items())
            and [(k, i) for k, i, _ in shown]
            == [(i["item_type"], i["id"]) for i in server["items"]],
            ", ".join(f"{k} {n}" for k, n in kinds.items()),
        )
        await page.locator("fieldset.facet[data-facet=epic] button", has_text=f"#{epic} ").click()
        under = await listed(page, 1 + 1 + 3 + 1)
        proof(
            "M13",
            "choosing an epic keeps its stories, tasks and rules in the server's order and drops "
            "the rest",
            [(k, i) for k, i, _ in under]
            == [
                pair
                for pair in [(i["item_type"], i["id"]) for i in server["items"]]
                if pair in {(k, i) for k, i, _ in under}
            ],
            f"{len(under)} of {len(shown)}",
        )


# -- 3 --------------------------------------------------------------------------------------------


async def overlapping(client: Client, page_url: str) -> None:
    epic_a, epic_b, firsts, seconds = await twin_epics(client, DUPLICATES)
    on_server = await every_overlap(client, epic_ids=[epic_a, epic_b])
    expected = [(p["first"]["id"], p["second"]["id"], p["similarity"]) for p in on_server["items"]]
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/overlaps?epics={epic_a},{epic_b}")
        shown = await pairs_on(page)
        both = {(a, b) for a, b, _ in shown if {a, b} <= {*firsts, *seconds}}
        proof(
            "M13",
            "the Overlaps page lists the M5 fixture's known duplicates and none of the unrelated "
            "pairs, with the same scores as MCP",
            shown == expected and both == set(zip(firsts, seconds, strict=True)),
            f"{len(both)} of 8 duplicates, {len(shown)} pairs",
        )
        pair = (firsts[1], seconds[1])
        before = await links_of(client, *pair)
        row = page.locator(f"li.pair[data-first='{pair[0]}'][data-second='{pair[1]}']")
        await page.locator("#overlap-min").fill("0.6")
        await page.locator("#apply-threshold").click()
        await expect(page.locator("#count")).to_contain_text("at 0.6 or more", timeout=SLOW)
        await pairs_on(page)
        await expect(row).to_be_visible(timeout=SLOW)
        untouched = await links_of(client, *pair) == before
        await row.get_by_role("button", name="Mark as duplicates").click()
        await expect(row.get_by_role("button", name="Already marked: duplicates")).to_be_disabled(
            timeout=SLOW
        )
        links = await links_of(client, *pair)
        proof(
            "M13",
            "nothing is linked until a click, and marking a pair as duplicates creates exactly "
            "one duplicates link, seen through list_links",
            untouched
            and before == {pair[0]: [], pair[1]: []}
            and links[pair[0]] == links[pair[1]] == [(pair[0], pair[1], "duplicates")],
            f"{pair[0]} -> {pair[1]}",
        )


# -- 4 --------------------------------------------------------------------------------------------

BODY = """# A heading {word}

- first point
- second point

```
code {word}
```

<script>window.hacked = 1</script>

<img src="http://other.invalid/p.png" onerror="window.hacked = 2">
"""


async def markdown(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Markdown {word}")
    story = await made(client, "story", f"Written {word}", epic, body=BODY.format(word=word))
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/board?epic={epic}&card={story}")
        text = card(page).locator(".body-text .markdown")
        await expect(text.locator("pre code")).to_have_text(f"code {word}", timeout=SLOW)
        shown = await text.inner_text()
        await page.wait_for_load_state("networkidle")
        foreign = [r for r in w.requests if not r.startswith(w.origin)]
        proof(
            "M13",
            "a body with a heading, a list, code, a script tag and an image tag from another "
            "address shows the structure and the two tags as literal text, and the browser makes "
            "no request to any other address",
            await text.locator("h4").inner_text() == f"A heading {word}"
            and await text.locator("ul > li").count() == 2
            and "<script>window.hacked = 1</script>" in shown
            and '<img src="http://other.invalid/p.png"' in shown
            and await text.locator("script, img").count() == 0
            and await page.evaluate("window.hacked === undefined")
            and foreign == []
            and w.dialogs == [],
            f"{len(w.requests)} requests, none elsewhere",
        )


# -- 5 --------------------------------------------------------------------------------------------


async def referring(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"References {word}")
    target = await made(client, "story", f"Target {word}", epic)
    rule = await ok(client, "create_rule", actor="a", title=f"Ruled {word}", story_id=target)
    missing = 987654321
    mentioner = await made(
        client,
        "story",
        f"Mentioner {word}",
        epic,
        body=f"See [[node:{target}]] and [[rule:{rule['id']}]] and [[node:{missing}]].",
    )
    before = await links_of(client, target, mentioner)
    status_before = (await ok(client, "get_node", node_id=mentioner))["status"]
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/board?epic={epic}&card={mentioner}")
        text = card(page).locator(".body-text .markdown")
        reference = text.locator(f".reference[data-kind=node][data-id='{target}']")
        await expect(reference).to_contain_text(f"Target {word}", timeout=SLOW)
        gone = text.locator(f".reference[data-id='{missing}']")
        missing_text = await gone.inner_text()
        await ok(
            client,
            "update_node",
            actor="a",
            node_id=target,
            expected_version=1,
            title=f"Renamed {word}",
        )
        await expect(reference).to_contain_text(f"Renamed {word}", timeout=SLOW)
        proof(
            "M13",
            "[[node:N]] shows as a link with the item's current title and follows a rename, a "
            "missing id is shown plainly as missing, and list_links and the status are unchanged",
            missing_text == f"[[node:{missing}]] (missing)"
            and await gone.get_attribute("href") is None
            and await links_of(client, target, mentioner) == before
            and (await ok(client, "get_node", node_id=mentioner))["status"] == status_before,
            f'"{missing_text}"',
        )

        await w.go(f"#/board?epic={epic}&card={target}")
        section = card(page).locator("section[data-part=mentions]")
        listed_by_server = await ok(client, "find_mentions", node_id=target)
        await expect(section.locator("li.mention")).to_have_count(
            len(listed_by_server["items"]), timeout=SLOW
        )
        shown = await section.locator("li.mention").evaluate_all(
            "els => els.map(e => e.dataset.source)"
        )
        expected = [
            f"{m['source']['item_type']}:{m['source']['id']}" for m in listed_by_server["items"]
        ]
        proof(
            "M13",
            '"Mentioned in" on a card equals find_mentions through MCP',
            sorted(shown) == sorted(expected) and f"story:{mentioner}" in shown,
            ", ".join(shown),
        )

        writer = await made(client, "story", f"Writer {word}", epic)
        await w.go(f"#/board?epic={epic}&card={writer}")
        box = page.locator("#edit-body")
        await box.click()
        await page.keyboard.type(f"Depends on [[renamed {word}")
        await expect(page.locator(".ref-picker")).to_be_visible(timeout=SLOW)
        await page.keyboard.press("Enter")
        typed = await box.input_value()
        saved_before = (await ok(client, "get_node", node_id=writer))["body"]
        proof(
            "M13",
            "the editor offers a pick-list as the person types [[ and writes only text: nothing "
            "is saved until Save, and no link is made",
            typed == f"Depends on [[node:{target}]]"
            and saved_before == ""
            and (await links_of(client, target, writer))[writer] == [],
            typed,
        )

        cited_in = await ok(
            client, "update_node", actor="a", node_id=writer, expected_version=1, body=typed
        )
        assert cited_in["version"] == 2
        await w.go(f"#/graph?epic={epic}&show=rules")
        svg = page.locator("svg#graph-svg")
        await expect(svg.locator(f".dot[data-key='n:{writer}']")).to_be_visible(timeout=SLOW)
        off = await svg.locator(".edge[data-type='mention']").count()
        await page.locator("[name=mentions]").check()
        edge = svg.locator(
            f".edge[data-type='mention'][data-source='n:{writer}'][data-target='n:{target}']"
        )
        await expect(edge).to_have_count(1, timeout=SLOW)
        proof(
            "M13",
            "the graph's mentions layer is off until chosen, then draws a line from the item "
            "whose text holds a reference to the item it names",
            off == 0,
            f"n:{writer} -> n:{target}",
        )


# -- 6 --------------------------------------------------------------------------------------------


async def jumping(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Switching {word}")
    story = await made(client, "story", f"Findable {word}", epic)
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        opened = []
        for name in PAGES:
            await w.open(f"#/{name}")
            await page.wait_for_selector("#page", timeout=SLOW)
            await page.keyboard.press("Control+k")
            await expect(page.locator("#switcher")).to_be_visible(timeout=SLOW)
            opened.append(
                await page.locator("#switcher-input").evaluate("e => e === document.activeElement")
            )
            await page.keyboard.press("Escape")
            await expect(page.locator("#switcher")).to_have_count(0)
        proof(
            "M13",
            "the quick switcher opens from the keyboard on every page, with its box ready to use",
            all(opened) and len(opened) == len(PAGES),
            f"{len(PAGES)} pages",
        )
        await w.go("#/table")
        await page.keyboard.press("Control+k")
        await page.keyboard.type(f"findable {word}")
        await expect(page.locator("#switcher [role=option]").first).to_have_attribute(
            "data-ref", f"node:{story}", timeout=SLOW
        )
        await page.keyboard.press("Enter")
        await expect(card(page)).to_contain_text(f"Findable {word}", timeout=SLOW)
        by_word = f"card={story}" in await page.evaluate("location.hash")
        await w.go("#/overlaps")
        await page.keyboard.press("Control+k")
        await page.keyboard.type(f"#{epic}")
        await expect(page.locator("#switcher [role=option]").first).to_have_attribute(
            "data-ref", f"node:{epic}", timeout=SLOW
        )
        await page.keyboard.press("Enter")
        await expect(card(page)).to_contain_text(f"Switching {word}", timeout=SLOW)
        proof(
            "M13",
            "the quick switcher jumps to an item by a word of its title and by its id",
            by_word and f"card={epic}" in await page.evaluate("location.hash"),
            f"#{story} by a word, #{epic} by id",
        )


# -- 7 --------------------------------------------------------------------------------------------


async def asking(client: Client, page_url: str) -> None:
    model = ScriptedModel()
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
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/search")
        before = (
            len((await ok(client, "list_decisions", limit=200))["items"]),
            len((await ok(client, "list_memories", limit=200))["items"]),
        )
        await page.locator("#ask-question").fill(question)
        await page.locator("#ask-send").click()
        answer = page.locator("#answer")
        await expect(answer.locator(".chip.generated")).to_be_visible(timeout=SLOW)
        reflected = await ok(client, "reflect", actor="check", question=question)
        relied = await page.locator("#relied-on li").evaluate_all(
            "els => els.map(e => e.dataset.relied)"
        )
        after = (
            len((await ok(client, "list_decisions", limit=200))["items"]),
            len((await ok(client, "list_memories", limit=200))["items"]),
        )
        proof(
            "M13",
            "with the scripted model, Ask shows the answer labelled generated, with the items it "
            "relied on as links, and stores nothing",
            await answer.locator(".chip.generated").inner_text() == "Generated, not a record"
            and f"{marker} invoices are PDFs" in await answer.inner_text()
            and relied == [f"{r['item_type']}:{r['id']}" for r in reflected["relied_on"]]
            and before == after,
            ", ".join(relied),
        )
        await page.locator("#belief-text").fill(f"{marker} invoices are PDFs, we think")
        await page.locator("#record-belief-from-answer").click()
        recorded = page.locator("#recorded-belief")
        await expect(recorded).to_be_visible(timeout=SLOW)
        belief_id = int(re.search(r"belief #(\d+)", await recorded.inner_text()).group(1))  # type: ignore[union-attr]
        belief: dict[str, Any] = await ok(client, "get_memory", memory_id=belief_id)
        proof(
            "M13",
            "Ask can record a belief stated by the typed name, resting on the items it relied on",
            belief["recorded_by"] == ADA
            and belief["origin"] == "stated"
            and {(e["type"], e["id"]) for e in belief["evidence"]}
            == {("decision", decision["id"]), ("fact", fact["id"])},
            f"belief #{belief_id}",
        )
    async with browser_page(board_url(NO_MODEL_URL), token=the_token(), name=ADA) as w:
        await w.open("#/search")
        await expect(w.page.locator("#ask-none")).to_be_visible(timeout=SLOW)
        says = await w.page.locator("#ask-none").inner_text()
        proof(
            "M13",
            "on a server with no language model the Ask box says so and offers no question box",
            "No language model is set up" in says
            and await w.page.locator("#ask-question").count() == 0,
            f'"{clip(says, 60)}"',
        )
