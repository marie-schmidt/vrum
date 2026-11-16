"""Chapter 8 of the acceptance scenario: the Board (M10, M14 and M16), driven in a real headless
browser against the real containers, with what happens read back through a real MCP client. Each
proof is shown the way a person would see it: what was done in the browser, and what the graph then
holds. The last part is M16's day of work, done with only Board, Graph and Roadmap as destinations,
in a project of its own so that every number can be exact."""

import asyncio
import datetime
import time

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import Locator, Page, expect

from tests.acceptance_scenario import URL, Failed, chapter, clip, proof
from tests.board_helpers import (
    GONE,
    SLOW,
    board_url,
    browser_page,
    cards_are,
    cell,
    create_in_browser,
    drag_bar,
    http,
    made,
    on,
    roadmap_ids,
    timeline,
)
from tests.helpers import (
    all_nodes,
    connect,
    day,
    embeddings_stopped,
    make_decision,
    make_fact,
    make_rule,
    ok,
    refused,
    reindexed,
    the_token,
    unique_word,
    whole_coverage_report,
)
from tests.world import make_project, register

ACTOR = "Ada Lovelace"
FREE = "kind:story,task status:ready -claimed"
TILES = ("free", "blocked", "overdue", "lapsed", "unplaced", "bare")


def same_words(board_words: str, mcp_words: str) -> bool:
    """The board said what the tool says, without the protocol's "Error executing tool"."""
    return bool(board_words.strip()) and mcp_words.endswith(board_words.strip())


async def board() -> None:
    chapter("The board: Board, Graph and Roadmap, for people next to the agents")
    page_url = board_url(URL)
    token = the_token()
    try:
        async with connect(URL, token) as client:
            await destinations(page_url, token)
            await agents_show_up(client, page_url, token)
            await attention()
            await people_edit(client, page_url, token)
            await refusals(client, page_url, token)
            await index_follows(client, page_url, token)
            await only_text(client, page_url, token)
        await days_work()
    except BrowserError as error:
        raise Failed(
            f"M16: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- three destinations, and where a project opens ------------------------------------------------


async def destinations(page_url: str, token: str) -> None:
    async with browser_page(page_url, token=token) as w:
        page = w.page
        heading = page.locator("main#page h1").first
        await w.open("")
        await expect(heading).to_have_text("Board", timeout=SLOW)
        landed = page.url.endswith("#/board")
        links = await page.locator("#main-nav a .side-label").all_inner_texts()
        beside = [
            await page.locator(selector).is_visible()
            for selector in ("#project-switcher", "#global-search", "#activity-open")
        ] + [await page.locator("#settings-menu").is_visible()]
        bounced = []
        for name in GONE:
            # An old address of a page that is gone is no alias: it lands on the Board.
            await w.go("#/graph")
            await expect(heading).to_have_text("Graph", timeout=SLOW)
            await w.go(f"#/{name}?epic=1&card=2")
            await expect(heading).to_have_text("Board", timeout=SLOW)
            bounced.append(page.url.endswith("#/board"))
        proof(
            "M16",
            "a project opens on the Board, Board, Graph and Roadmap are the only destinations in "
            "the sidebar's views list, with the search box, Activity and Settings beside them, "
            "and the old addresses of Home, Table, Outline, Rules, Memory, Overlaps and Ask are "
            "not aliased",
            landed and links == ["Board", "Graph", "Roadmap"] and all(beside) and all(bounced),
            f"{len(GONE)} old addresses all land on the Board",
        )
        assert w.problems == [], w.problems


# -- what agents do shows up -------------------------------------------------------------------


async def agents_show_up(client: Client, page_url: str, token: str) -> None:
    word = unique_word()
    async with browser_page(page_url, token=token) as w:
        await w.open("#/board")
        await expect(w.page.locator(".board")).to_be_visible()
        marker = await w.page.evaluate("window.__same = 'same page'; window.__same")
        epic = await made(client, "epic", f"Billing {word}")
        story = await made(client, "story", f"Invoices {word}", epic)
        first = await made(client, "task", f"Template {word}", story)
        second = await made(client, "task", f"Send it {word}", story)
        await ok(
            client, "add_link", actor="agent-1", source_id=second, target_id=first,
            link_type="needs_first",
        )  # fmt: skip
        await ok(client, "claim_node", actor="agent-2", node_id=first)
        await ok(
            client, "update_node", actor="agent-2", node_id=first, expected_version=1,
            status="in_progress",
        )  # fmt: skip
        started = time.monotonic()
        await expect(
            cell(w.page, epic, "ready").locator(f".card[data-id='{story}']")
        ).to_be_visible(timeout=SLOW)
        await expect(
            cell(w.page, epic, "in_progress").locator(f".card[data-id='{first}']")
        ).to_contain_text("Held by agent-2")
        await expect(
            cell(w.page, epic, "new").locator(f".card[data-id='{second}']")
        ).to_contain_text(f"Waiting for #{first} Template {word}")
        seconds = time.monotonic() - started
        proof(
            "M10",
            "an epic, story, task, link and claim made through MCP show on the board with no "
            "reload, in the right lane and column, with who holds it and what blocks it",
            seconds < 10 and await w.page.evaluate("window.__same") == marker,
            f"{seconds:.1f} seconds, the same page",
        )


# -- what needs attention, as the Board says it ------------------------------------------------


async def tile(page: Page, key: str) -> int:
    link = page.locator(f"#attention a.attention-item[data-key={key}]")
    await expect(link).to_be_visible(timeout=SLOW)
    return int(await link.get_attribute("data-value") or "-1")


async def asked(client: Client) -> dict[str, int]:
    """What the tools say to each question the Board's strip answers."""
    coverage = await ok(client, "coverage_report", show="both", limit=1)

    async def total(query: str) -> int:
        return int((await ok(client, "list_nodes", query=query, limit=1))["total"])

    return {
        "free": await total(FREE),
        "blocked": await total("status:blocked"),
        "overdue": await total("is:overdue"),
        "lapsed": await total("is:lapsed"),
        "unplaced": coverage["unplaced_rule_count"],
        "bare": coverage["story_without_rules_count"],
    }


async def busy_project(client: Client) -> int:
    """Work in every state the strip counts, a requirement in no story, a story with no
    requirement. Gives the epic."""
    word = unique_word()
    epic = await made(client, "epic", f"Counted {word}")
    story = await made(client, "story", f"Story {word}", epic)
    tasks = {
        name: await made(client, "task", f"{name} {word}", story, **more)
        for name, more in (
            ("late", {"target_on": day(-3)}),
            ("stuck", {}),
            ("going", {}),
            ("finished", {}),
            ("dropped", {}),
            ("held", {}),
            ("lapsing", {}),
            ("free", {}),
            ("waiting", {}),
        )
    }
    for name, status in (
        ("stuck", "blocked"),
        ("going", "in_progress"),
        ("finished", "done"),
        ("dropped", "dropped"),
    ):
        await ok(
            client, "update_node", actor="a", node_id=tasks[name], expected_version=1, status=status
        )
    await ok(
        client, "add_link", actor="a", source_id=tasks["waiting"], target_id=tasks["free"],
        link_type="needs_first",
    )  # fmt: skip
    await ok(client, "claim_node", actor=f"holder-{word}", node_id=tasks["held"], lease_seconds=600)
    await ok(
        client, "claim_node", actor=f"quitter-{word}", node_id=tasks["lapsing"], lease_seconds=1
    )
    await make_rule(client, f"Unplaced {word}")
    placed = await made(client, "story", f"Has a requirement {word}", epic)
    await make_rule(client, f"Placed {word}", story_id=placed)
    await asyncio.sleep(1.5)  # a claim of one second has lapsed
    return epic


async def attention() -> None:
    project = make_project(register(), "Attention")
    async with (
        connect(project.mcp_url, project.key) as client,
        browser_page(project.address, token=project.key) as w,
    ):
        page = w.page
        await busy_project(client)
        expected = await asked(client)
        await w.open("")
        shown = {key: await tile(page, key) for key in TILES}
        proof(
            "M14",
            "the Board says what needs attention in numbers that equal what MCP gives for the "
            "same questions: ready work nobody holds, blocked, overdue, claims that ran out with "
            "no handoff, requirements without a story, stories with no requirement",
            shown == expected and all(value >= 1 for value in expected.values()),
            ", ".join(f"{key} {value}" for key, value in shown.items()),
        )

        async def follow(key: str) -> None:
            await w.go("#/board")
            link = page.locator(f"#attention a.attention-item[data-key={key}]")
            await expect(link).to_be_visible(timeout=SLOW)
            await link.click()

        landed = {}
        for key, query in (
            ("free", FREE),
            ("blocked", "status:blocked"),
            ("overdue", "is:overdue"),
            ("lapsed", "is:lapsed"),
        ):
            await follow(key)
            matched = await all_nodes(client, query=query)
            await expect(page.locator("#filter")).to_have_value(query, timeout=SLOW)
            await cards_are(page, {n["id"] for n in matched if n["kind"] != "epic"})
            landed[key] = len(matched)
        report = await whole_coverage_report(client)
        await follow("unplaced")
        summary = page.locator("#requirements-summary")
        await expect(summary).to_have_attribute("data-unplaced", str(expected["unplaced"]))
        unplaced = page.locator("#unplaced-group li[data-kind=rule]")
        await expect(unplaced).to_have_count(expected["unplaced"], timeout=SLOW)
        unplaced_ids = {
            int(await row.get_attribute("data-id") or 0) for row in await unplaced.all()
        }
        await follow("bare")
        part = page.locator("#panel [data-part=stories-without-requirements]")
        await expect(part.locator("h3")).to_have_text(
            f"Stories with no requirement ({expected['bare']})", timeout=SLOW
        )
        bare_ids = {
            int(await row.get_attribute("data-story") or 0)
            for row in await part.locator("li").all()
        }
        proof(
            "M14",
            "following a number lands on exactly those items: the Board filtered by the very "
            "question, the requirements in no story on the Graph's context list, and the stories "
            "with no requirement in the coverage report",
            landed == {k: expected[k] for k in landed}
            and unplaced_ids == {rule["id"] for rule in report["unplaced_rules"]}
            and bare_ids == {story["id"] for story in report["stories_without_rules"]},
            f"{sum(landed.values())} cards, {len(unplaced_ids)} requirements, "
            f"{len(bare_ids)} stories",
        )
        assert w.problems == [], w.problems


# -- what a person does is what the graph holds ----------------------------------------------


async def people_edit(client: Client, page_url: str, token: str) -> None:
    word = unique_word()
    async with browser_page(page_url, token=token, name=None) as w:
        page = w.page
        await w.open("#/board")
        await page.locator("#who").click()
        await page.locator("dialog.modal #name").fill(ACTOR)
        await page.locator("dialog.modal").get_by_role("button", name="Save").click()
        await expect(page.locator("#who")).to_contain_text(ACTOR)
        epic = await create_in_browser(page, "epic", f"Portal {word}")
        story = await create_in_browser(page, "story", f"Sign in {word}", epic)
        other = await create_in_browser(page, "story", f"Profile {word}", epic)
        task = await create_in_browser(page, "task", f"Login form {word}", story)
        friend = await create_in_browser(page, "task", f"Logout {word}", story)
        await w.go(f"#/board?epic={epic}&sel=node:{task}")
        await expect(page.locator("#card-title")).to_contain_text(f"#{task} ")
        await page.locator("#edit-title").fill(f"Login form, better {word}")
        await page.locator("#edit-body").fill("Email and password, then a code.")
        await page.locator("#edit-priority").select_option("high")
        await page.get_by_role("button", name="Save changes").click()
        await expect(page.locator("#panel .card-detail")).to_contain_text("Email and password")
        await page.drag_and_drop(
            f".card[data-id='{task}']", f".cell[data-epic='{epic}'][data-status='in_progress']"
        )
        await expect(
            cell(page, epic, "in_progress").locator(f".card[data-id='{task}']")
        ).to_be_visible()
        # With the panel open the columns are wider than the room: scroll the target into view.
        await cell(page, epic, "done").scroll_into_view_if_needed()
        await page.drag_and_drop(
            f".card[data-id='{task}']", f".cell[data-epic='{epic}'][data-status='done']"
        )
        await expect(cell(page, epic, "done").locator(f".card[data-id='{task}']")).to_be_visible()
        await w.go(f"#/board?epic={epic}&sel=node:{task}")
        await expect(page.locator("#panel .card-detail .chips")).to_contain_text("Done")
        await page.locator("#move-parent").select_option(str(other))
        await page.get_by_role("button", name="Move", exact=True).click()
        await expect(page.locator("#panel .card-detail > p", has_text="In story")).to_contain_text(
            f"#{other} Profile"
        )
        await page.locator("#link-relation").select_option("relates_to:out")
        await page.locator("#link-target").fill(f"#{friend}")
        await page.get_by_role("button", name="Add", exact=True).click()
        await expect(page.locator("#panel .remove-link")).to_have_count(1)
        await page.locator("#panel .remove-link").click()
        await expect(page.locator("#panel .remove-link")).to_have_count(0)
        assert w.problems == [], w.problems

    final = await ok(client, "get_node", node_id=task)
    events = (await ok(client, "node_history", node_id=task, limit=200))["items"]
    listed = [n["id"] for n in (await ok(client, "list_nodes", parent_id=other))["items"]]
    links = (await ok(client, "list_links", node_id=task))["items"]
    proof(
        "M10",
        "what a person does in the browser (create, edit, drag to in progress and done, move, add "
        "and remove a link) is what get, list and history then show, with their name and versions",
        (final["title"], final["body"], final["priority"], final["status"])
        == (f"Login form, better {word}", "Email and password, then a code.", "high", "done")
        and (final["parent_id"], final["version"]) == (other, 5)
        and listed == [task]
        and links == []
        and [(e["action"], e["actor"]) for e in events]
        == [
            (a, ACTOR)
            for a in ("create", "update", "update", "update", "move", "link_added", "link_removed")
        ]
        and events[3]["before"] == {"status": "in_progress", "version": 3}
        and events[3]["after"] == {"status": "done", "version": 4},
        f"{len(events)} events, version {final['version']}",
    )


# -- what the server refuses, the board refuses in the same words -----------------------------


async def refusals(client: Client, page_url: str, token: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Refusals {word}")
    story = await made(client, "story", f"Story {word}", epic)
    first = await made(client, "task", f"First {word}", story)
    second = await made(client, "task", f"Second {word}", story)
    dated = await made(client, "task", f"Dated {word}", story, start_on=day(2), target_on=day(9))
    await ok(
        client, "add_link", actor="a", source_id=second, target_id=first, link_type="needs_first"
    )
    say_ready = await refused(
        client, "update_node", actor="x", node_id=second, expected_version=1, status="ready"
    )
    say_cycle = await refused(
        client, "add_link", actor="x", source_id=first, target_id=second, link_type="needs_first"
    )
    say_blank = await refused(
        client, "update_node", actor="  ", node_id=story, expected_version=1, title="x"
    )
    say_dates = await refused(
        client, "update_node", actor="x", node_id=dated, expected_version=1, start_on=day(12)
    )
    before = (
        await ok(client, "get_node", node_id=second),
        await ok(client, "list_links", node_id=first),
        await ok(client, "get_node", node_id=dated),
    )
    async with browser_page(page_url, token=token) as w:
        page = w.page
        await w.open(f"#/board?epic={epic}")
        await expect(cell(page, epic, "new").locator(f".card[data-id='{second}']")).to_be_visible()
        await page.drag_and_drop(
            f".card[data-id='{second}']", f".cell[data-epic='{epic}'][data-status='ready']"
        )
        ready_words = await page.locator(".notice.refusal .notice-text").first.inner_text()
        proof(
            "M10",
            "dropping a card on ready is refused with the words update_node gives, and the card "
            "stays",
            same_words(ready_words, say_ready),
            f'"{clip(ready_words, 80)}"',
        )
        await w.go(f"#/board?epic={epic}&sel=node:{first}")
        await page.locator("#link-relation").select_option("needs_first:out")
        await page.locator("#link-target").fill(f"#{second}")
        await page.get_by_role("button", name="Add", exact=True).click()
        cycle = page.locator("#panel .inline-form .problem").last
        await expect(cycle).not_to_be_empty()
        cycle_words = await cycle.inner_text()
        proof(
            "M10",
            "a link that makes a cycle is refused with the words add_link gives, and what was "
            "typed stays",
            same_words(cycle_words, say_cycle)
            and await page.locator("#link-target").input_value() == f"#{second}",
            f'"{clip(cycle_words, 80)}"',
        )
        await w.go(f"#/board?epic={epic}&sel=node:{story}")
        title = page.locator("#edit-title")
        await title.fill("My title")
        await ok(
            client, "update_node", actor="agent-1", node_id=story, expected_version=1,
            title="Agent title",
        )  # fmt: skip
        await expect(page.locator("#panel .notice.changed")).to_be_visible(timeout=SLOW)
        say_stale = await refused(
            client, "update_node", actor="x", node_id=story, expected_version=1, title="y"
        )
        await page.get_by_role("button", name="Save changes").click()
        stale = page.locator("#panel .edit .problem .refusal-text")
        await expect(stale).to_be_visible()
        stale_words = await stale.inner_text()
        kept = await title.input_value()
        await page.locator("#panel .edit .problem .load-current").click()
        await expect(page.locator("#edit-title")).to_have_value("Agent title")
        proof(
            "M10",
            "an edit from a stale version is refused with the stale-version words, keeps what was "
            "typed, and offers the current version",
            same_words(stale_words, say_stale) and kept == "My title",
            f'"{clip(stale_words, 80)}"',
        )
        await page.evaluate("localStorage.setItem('vrum.actor', '   ')")
        await page.locator("#edit-title").fill("A third title")
        await page.get_by_role("button", name="Save changes").click()
        blank = page.locator("#panel .edit .problem .refusal-text")
        await expect(blank).to_be_visible()
        blank_words = await blank.inner_text()
        proof(
            "M10",
            "a blank name is refused with the words update_node gives",
            same_words(blank_words, say_blank),
            f'"{clip(blank_words, 80)}"',
        )
        await page.evaluate("localStorage.setItem('vrum.actor', 'Tester')")
        await w.go(f"#/board?epic={epic}&sel=node:{dated}")
        await page.locator("#edit-start").fill(day(12))
        await page.get_by_role("button", name="Save changes").click()
        late = page.locator("#panel .edit .problem .refusal-text")
        await expect(late).to_be_visible(timeout=SLOW)
        late_words = await late.inner_text()
        proof(
            "M1A",
            "a start date after the target typed in the card is refused with the words "
            "update_node gives, and what was typed stays",
            same_words(late_words, say_dates)
            and await page.locator("#edit-start").input_value() == day(12),
            f'"{clip(late_words, 80)}"',
        )
    after = (
        await ok(client, "get_node", node_id=second),
        await ok(client, "list_links", node_id=first),
        await ok(client, "get_node", node_id=dated),
    )
    proof(
        "M10",
        "none of the five refusals changed the graph",
        after == before and (await ok(client, "get_node", node_id=story))["title"] == "Agent title",
    )


# -- the index follows the graph -------------------------------------------------------------


async def waiting(client: Client) -> int:
    page = await ok(client, "search", query="anything at all", mode="words", limit=1)
    return int(page["index"]["waiting_to_index"])


async def settle(client: Client) -> None:
    deadline = time.monotonic() + 30
    while await waiting(client):
        if time.monotonic() > deadline:
            raise Failed("M10: the index did not catch up on its own")
        await asyncio.sleep(0.5)


async def index_follows(client: Client, page_url: str, token: str) -> None:
    word, later = unique_word(), unique_word()
    epic = await made(client, "epic", f"Index {word}")
    story = await made(client, "story", f"Indexed story {word}", epic)
    await reindexed(client)
    async with browser_page(page_url, token=token) as w:
        page = w.page
        await w.open(f"#/board?epic={epic}&sel=node:{story}")
        await page.locator("#edit-body").fill(f"The phrase {word} appears here")
        await page.get_by_role("button", name="Save changes").click()
        await expect(page.locator("#panel .card-detail")).to_contain_text(f"The phrase {word}")
        found = await ok(client, "search", query=word, mode="words")
        by_words = [i["id"] for i in found["items"] if i["item_type"] == "story"] == [story]
        await settle(client)
        await expect(page.locator("#index-state")).to_have_text("Index: 0 waiting", timeout=SLOW)
        proof(
            "M10",
            "a story edited on the board is found by search at once, and the waiting count reaches "
            "zero with no manual reindex",
            by_words,
        )
        with embeddings_stopped():
            before = await waiting(client)
            await page.locator("#edit-body").fill(f"The second phrase {later} appears here")
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator("#panel .card-detail")).to_contain_text(
                f"second phrase {later}"
            )
            saved = (
                f"second phrase {later}" in (await ok(client, "get_node", node_id=story))["body"]
            )
            one = await waiting(client) == before + 1
            await expect(page.locator("#index-state")).to_have_text(
                "Index: 1 waiting", timeout=SLOW
            )
        await settle(client)
        await expect(page.locator("#index-state")).to_have_text("Index: 0 waiting", timeout=SLOW)
        again = await ok(
            client, "search", query=f"Indexed story {word} The second phrase {later} appears here"
        )
        proof(
            "M10",
            "with the embedding service stopped the edit still saves and the board and search both "
            "say one item waits; when it is back the count reaches zero and the item is found by "
            "meaning",
            saved and one and {i["id"]: i["matched"] for i in again["items"]}[story] == "both",
        )


# -- what is shown is only text, and it all comes from the server's own address -------------------


async def only_text(client: Client, page_url: str, token: str) -> None:
    word = unique_word()
    markup = f'<img src=x onerror="window.__ran=1"><script>window.__ran=2</script> {word}'
    epic = await made(client, "epic", markup, body="<b>bold</b> <script>window.__ran=3</script>")
    story = await made(client, "story", f"<i>{word}</i>", epic)
    fact = await make_fact(
        client, f'<img src=x onerror="window.__ran=4"> a fact {word}',
        source=f"<b>the source</b> {word}", node_ids=[story],
    )  # fmt: skip
    decision = await make_decision(
        client, f"<u>A decision</u> {word}",
        text="<script>window.__ran=5</script> what was decided",
        reason='<img src=x onerror="window.__ran=6"> why', node_ids=[story],
    )  # fmt: skip
    _, headers, _ = http("GET", page_url)
    async with browser_page(page_url, token=token) as w:
        page = w.page
        await w.open(f"#/board?epic={epic}")
        await expect(page.locator(f"section.lane[data-epic='{epic}']")).to_contain_text(markup)
        await w.go(f"#/board?epic={epic}&sel=fact:{fact['id']}")
        await expect(page.locator("#panel .record-detail .source")).to_contain_text(
            f"<b>the source</b> {word}", timeout=SLOW
        )
        await w.go(f"#/board?epic={epic}&sel=decision:{decision['id']}")
        await expect(page.locator("#panel .record-detail .reason")).to_contain_text(
            '<img src=x onerror="window.__ran=6"> why', timeout=SLOW
        )
        await w.go(f"#/board?epic={epic}&drawer=search&search={word}&search_mode=words")
        await expect(page.locator("#search-results li.result")).to_have_count(4, timeout=SLOW)
        await w.go(f"#/graph?epic={epic}")
        await expect(page.locator(f".dot[data-key='node:{epic}']")).to_be_visible(timeout=SLOW)
        ran = await page.evaluate("typeof window.__ran")
        stray = await page.locator("img[src='x'], script:not([src]), b, u").count()
        policy = headers["content-security-policy"]
        proof(
            "M10",
            "a title, body, fact source and decision reason with HTML and script are shown as "
            "literal text in the board, the panel, the search drawer and the graph and nothing "
            "runs; every request goes to the server's own address, and the content security "
            "policy is set",
            ran == "undefined"
            and stray == 0
            and w.dialogs == []
            and all(u.startswith(w.origin) for u in w.requests)
            and "default-src 'none'" in policy
            and "'unsafe-inline'" not in policy,
            f"{len(w.requests)} requests, all to {w.origin}",
        )
        assert w.problems == [], w.problems


# -- M16: a day's work with Board, Graph and Roadmap ------------------------------------------


async def record_ids(panel: Locator, section: str) -> list[int]:
    """The ids of the facts or decisions in one section of the recall the panel shows."""
    numbers: list[int] = await panel.locator(f"[data-section={section}] li.record").evaluate_all(
        "els => els.map(e => Number(e.dataset.record.split(':')[1]))"
    )
    return numbers


async def attempt_new(page: Page, kind: str, title: str) -> str:
    """Fill the New item form with no parent to choose from, and give what it says."""
    await page.locator("#new-item").click()
    dialog = page.locator("dialog.modal")
    await dialog.locator("#new-kind").select_option(kind)
    await dialog.locator("#new-title").fill(title)
    await dialog.get_by_role("button", name="Create").click()
    problem = dialog.locator("p.problem")
    await expect(problem).not_to_be_empty(timeout=SLOW)
    words = await problem.inner_text()
    await dialog.get_by_role("button", name="Cancel").click()
    return words


async def days_work() -> None:
    word = unique_word()
    project = make_project(register(), f"Daily {word}")
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=ACTOR) as w,
    ):
        page = w.page
        panel = page.locator("#panel")

        # -- the Board: plan the work, in an empty project
        await w.open("")
        await expect(page.locator("h1")).to_have_text("Board", timeout=SLOW)
        await expect(page.locator("#board-empty")).to_be_visible(timeout=SLOW)
        said_story = await refused(agent, "create_node", actor="x", kind="story", title="Orphan")
        story_words = await attempt_new(page, "story", f"Orphan story {word}")
        epic = await create_in_browser(page, "epic", f"Billing {word}")
        said_task = await refused(agent, "create_node", actor="x", kind="task", title="Orphan")
        task_words = await attempt_new(page, "task", f"Orphan task {word}")
        story = await create_in_browser(page, "story", f"Invoices {word}", epic)
        task = await create_in_browser(page, "task", f"Template {word}", story)
        later = await create_in_browser(page, "task", f"Send them {word}", story)
        listed = {n["id"]: n for n in (await ok(agent, "list_nodes", limit=200))["items"]}
        for node_id in (story, task, later):
            await expect(page.locator(f".card[data-id='{node_id}']")).to_have_attribute(
                "data-status", listed[node_id]["status"]
            )
        await page.locator(f".card[data-id='{story}']").click()
        await expect(page.locator("#card-title")).to_contain_text(f"#{story} ", timeout=SLOW)
        await page.locator("#panel button.add-requirement").click()
        dialog = page.locator("dialog.modal")
        await expect(dialog.locator("#requirement-story")).to_have_value(str(story))
        await dialog.locator("#requirement-title").fill(f"Every invoice shows its VAT {word}")
        await dialog.get_by_role("button", name="Create the requirement").click()
        row = panel.locator("[data-part=requirements] li.requirement")
        await expect(row).to_have_count(1, timeout=SLOW)
        rule_id = int(await row.get_attribute("data-rule") or 0)
        rule = await ok(agent, "get_rule", rule_id=rule_id)
        history_of_rule = (await ok(agent, "rule_history", rule_id=rule_id))["items"]
        await expect(page.locator("#attention a[data-key=bare]")).to_have_attribute(
            "data-value", "0", timeout=SLOW
        )
        proof(
            "M16",
            "the day starts on the Board in an empty project: a story or task with no parent is "
            "refused in the tool's words, an epic, a story and two tasks made there are the ones "
            "MCP lists, and a requirement is placed on the story from its panel by the person",
            same_words(story_words, said_story)
            and same_words(task_words, said_task)
            and set(listed) == {epic, story, task, later}
            and [listed[i]["kind"] for i in (epic, story, task, later)]
            == ["epic", "story", "task", "task"]
            and (listed[story]["parent_id"], listed[task]["parent_id"]) == (epic, story)
            and (rule["story_id"], rule["title"]) == (story, f"Every invoice shows its VAT {word}")
            and history_of_rule[0]["actor"] == ACTOR,
            f"epic {epic}, story {story}, tasks {task} and {later}, requirement {rule_id}",
        )

        # -- what agents know: facts and decisions, then ready work claimed through MCP
        fact = await make_fact(
            agent, f"VAT is 19 percent {word}", source="The tax office site", node_ids=[task]
        )
        decision = await make_decision(agent, f"Round per line {word}", node_ids=[task])
        wide = await make_decision(agent, f"Always in euros {word}", project_wide=True)
        taken = await ok(agent, "next_work", actor="agent-keyed")
        briefing = taken["context"]
        await expect(page.locator(f".card[data-id='{task}']")).to_contain_text(
            "Held by agent-keyed", timeout=SLOW
        )
        await page.locator(f".card[data-id='{task}']").click()
        await page.locator("#activity-open").click()
        drawer = page.locator("#drawer[data-drawer=activity]")
        await expect(drawer).to_be_visible(timeout=SLOW)
        claim = drawer.locator(f"#claims-live .claim[data-node='{task}']")
        await expect(claim).to_have_attribute("data-actor", "agent-keyed", timeout=SLOW)
        await expect(claim).to_have_attribute("data-live", "1")
        await expect(
            drawer.locator("#feed li.event[data-action=claimed]", has_text=f"#{task}").first
        ).to_contain_text("agent-keyed", timeout=SLOW)
        working = await ok(agent, "who_is_working")
        mine = next(a for a in working["agents"] if a["actor"] == "agent-keyed")
        await expect(panel.locator(".card-detail")).to_contain_text("Held by agent-keyed")

        # -- the panel holds the context the agent was briefed with, and the history
        await expect(panel.locator("[data-section=facts] li.record")).to_have_count(1)
        recalled = await ok(agent, "recall", node_id=task, limit=50)

        def held(section: str) -> list[int]:
            return [
                (item.get("fact") or item["decision"])["id"] for item in recalled[section]["items"]
            ]

        shown = {
            section: await record_ids(panel, section)
            for section in ("facts", "active_decisions", "project_wide_decisions")
        }
        fact_row = panel.locator(f"li.record[data-record='fact:{fact['id']}']")
        await expect(fact_row).to_have_attribute("data-confirmation", "unconfirmed")
        await expect(fact_row).to_contain_text("Source: The tax office site")
        proof(
            "M16",
            "ready work claimed through MCP shows on its card, in the panel and as a live claim "
            "in the Activity drawer, where who is working on what is the server's own list",
            taken["work"]["id"] == task
            and [h["node_id"] for h in mine["holding"]] == [task]
            and [r["id"] for r in briefing["rules"]] == [rule_id],
            "held by agent-keyed",
        )
        proof(
            "M16",
            "the work's context is read in the panel: its facts, decisions and every project-wide "
            "decision are exactly what recall says and what the agent was briefed with, the fact "
            "shown unconfirmed with its source",
            shown["facts"] == held("facts") == [fact["id"]] == [f["id"] for f in briefing["facts"]]
            and shown["active_decisions"]
            == held("active_decisions")
            == [decision["id"]]
            == [d["id"] for d in briefing["active_decisions"]]
            and shown["project_wide_decisions"] == held("project_wide_decisions")
            and wide["id"] in shown["project_wide_decisions"]
            and wide["id"] in [d["id"] for d in briefing["project_wide_decisions"]],
            f"{len(shown['facts'])} fact, {len(shown['active_decisions'])} decision, "
            f"{len(shown['project_wide_decisions'])} project-wide",
        )

        # -- the handoff
        await ok(
            agent, "finish_work", actor="agent-keyed", node_id=task, outcome="done",
            note=f"Built the template; VAT is a field, not a constant {word}.",
        )  # fmt: skip
        handoff = drawer.locator("#feed li.event[data-action=handoff]", has_text=f"#{task}")
        await expect(handoff.first).to_contain_text("agent-keyed", timeout=SLOW)
        await expect(handoff.first).to_contain_text(f"VAT is a field, not a constant {word}")
        await expect(claim).to_have_count(0, timeout=SLOW)
        await expect(cell(page, epic, "done").locator(f".card[data-id='{task}']")).to_be_visible(
            timeout=SLOW
        )
        last = panel.locator("[data-part=handoff]")
        await expect(last).to_contain_text(f"VAT is a field, not a constant {word}", timeout=SLOW)
        events = (await ok(agent, "node_history", node_id=task, limit=200))["items"]
        in_panel = await panel.locator("[data-part=history] li").evaluate_all(
            "els => els.map(e => Number(e.dataset.event))"
        )
        proof(
            "M16",
            "after finish_work the handoff note is in the drawer in full and in the panel, the "
            "claim is gone and the card is in Done, and the panel's history is the node's "
            "history, newest first",
            {"create", "claimed", "handoff"} <= {e["action"] for e in events}
            and sorted(in_panel) == sorted(e["id"] for e in events)
            and in_panel == sorted(in_panel, reverse=True)
            and (await ok(agent, "get_node", node_id=task))["status"] == "done",
            f"{len(events)} events",
        )

        # -- the Graph: the same work and context, in the same panel
        await page.locator("#drawer-close").click()
        await page.locator("#main-nav a[data-page=graph]").click()
        await expect(page.locator("#graph-page")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#card-title")).to_contain_text(f"#{task} ")
        dot = page.locator(f".dot[data-key='node:{task}']")
        await expect(dot).to_have_attribute("data-status", "done", timeout=SLOW)
        await page.locator("#graph-view-context").click()
        for key in (f"fact:{fact['id']}", f"decision:{decision['id']}", f"rule:{rule_id}"):
            await expect(page.locator(f".dot[data-key='{key}']")).to_be_visible(timeout=SLOW)

        # -- refusals, from the existing owners: a cycle, a stale edit, a claim, a date
        await ok(
            agent, "add_link", actor="a", source_id=later, target_id=task, link_type="needs_first"
        )
        say_cycle = await refused(
            agent, "add_link", actor=ACTOR, source_id=task, target_id=later, link_type="needs_first"
        )
        before = {i: await ok(agent, "get_node", node_id=i) for i in (task, later)}
        await w.go(f"#/board?epic={epic}&sel=node:{task}")
        await expect(page.locator("#card-title")).to_contain_text(f"#{task} ", timeout=SLOW)
        await page.locator("#link-relation").select_option("needs_first:out")
        await page.locator("#link-target").fill(f"#{later}")
        await page.get_by_role("button", name="Add", exact=True).click()
        problem = page.locator("#panel .inline-form .problem").last
        await expect(problem).not_to_be_empty(timeout=SLOW)
        cycle_words = await problem.inner_text()
        await w.go(f"#/board?epic={epic}&sel=node:{later}")
        await expect(page.locator("#card-title")).to_contain_text(f"#{later} ", timeout=SLOW)
        await page.locator("#edit-title").fill(f"My title {word}")
        version = (await ok(agent, "get_node", node_id=later))["version"]
        await ok(
            agent, "update_node", actor="agent-keyed", node_id=later, expected_version=version,
            title=f"Agent title {word}",
        )  # fmt: skip
        say_stale = await refused(
            agent, "update_node", actor=ACTOR, node_id=later, expected_version=version, title="x"
        )
        await page.get_by_role("button", name="Save changes").click()
        stale = page.locator("#panel .edit .problem .refusal-text")
        await expect(stale).to_be_visible(timeout=SLOW)
        stale_words = await stale.inner_text()
        await ok(agent, "claim_node", actor="agent-b", node_id=later)
        say_claim = await refused(agent, "claim_node", actor=ACTOR, node_id=later)
        await page.locator("#activity-open").click()
        await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible(timeout=SLOW)
        await page.locator("#claim-id").fill(f"#{later}")
        await page.locator("#claim-by-id-go").click()
        refusal = page.locator(".notice.refusal .notice-text").first
        await expect(refusal).to_be_visible(timeout=SLOW)
        claim_words = await refusal.inner_text()
        held_by = (await ok(agent, "get_node", node_id=later))["claimed_by"]
        await ok(agent, "release_claim", actor="agent-b", node_id=later, note="Not today.")
        await page.locator("#drawer-close").click()
        proof(
            "M16",
            "a dependency cycle, a stale edit and a claim another agent holds are refused in the "
            "words of the tools that own them, and nothing the person typed is lost",
            same_words(cycle_words, say_cycle)
            and same_words(stale_words, say_stale)
            and same_words(claim_words, say_claim)
            and "agent-b" in say_claim
            and held_by == "agent-b"
            and (await ok(agent, "get_node", node_id=task))["version"] == before[task]["version"]
            and await page.locator("#edit-title").input_value() == f"My title {word}"
            and (await ok(agent, "get_node", node_id=later))["title"] == f"Agent title {word}",
            f'"{clip(claim_words, 70)}"',
        )

        # -- the Roadmap: schedule the work
        await page.locator("#main-nav a[data-page=roadmap]").click()
        await expect(page.locator("h1")).to_have_text("Roadmap", timeout=SLOW)
        await w.go(f"#/roadmap?q=epic:{epic}&sel=node:{task}")
        await expect(page.locator(f".tray-item[data-id='{later}']")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#card-title")).to_contain_text(f"#{task} ", timeout=SLOW)
        line = await timeline(page)
        first_start = line["today"] + datetime.timedelta(days=2)
        await page.locator("#edit-start").fill(first_start.isoformat())
        await page.locator("#edit-target").fill((first_start + datetime.timedelta(6)).isoformat())
        await page.get_by_role("button", name="Save changes").click()
        await expect(page.locator(f".rm-bar[data-id='{task}']")).to_be_visible(timeout=SLOW)
        at = ((line["today"] - line["origin"]).days + 12) * line["ppd"]
        await page.drag_and_drop(
            f".tray-item[data-id='{later}'] .tray-drag",
            ".rm-rows",
            target_position={"x": at + line["ppd"] / 2, "y": 60},
        )
        await expect(page.locator(f".rm-bar[data-id='{later}']")).to_be_visible(timeout=SLOW)
        scheduled = {i: await ok(agent, "get_node", node_id=i) for i in (task, later)}
        bars_say = []
        by_whom = []
        for node_id in (task, later):
            bar = page.locator(f".rm-bar[data-id='{node_id}']")
            bars_say.append(
                (await bar.get_attribute("data-start"), await bar.get_attribute("data-end"))
                == (scheduled[node_id]["start_on"], scheduled[node_id]["target_on"])
            )
            changes = [
                e
                for e in (await ok(agent, "node_history", node_id=node_id, limit=200))["items"]
                if e["action"] == "update" and "start_on" in e["after"]
            ]
            by_whom.append(
                [(e["actor"], e["before"]["start_on"]) for e in changes] == [(ACTOR, None)]
            )
        arrow = page.locator(f"path.dep[data-from='{task}'][data-to='{later}']")
        await expect(arrow).to_have_count(1)
        placed = await roadmap_ids(page, 4, 0)
        node = scheduled[later]
        too_late = (on(node["start_on"]) + datetime.timedelta(days=30)).isoformat()
        say_date = await refused(
            agent, "update_node", actor=ACTOR, node_id=later,
            expected_version=node["version"], start_on=too_late,
        )  # fmt: skip
        await drag_bar(page, later, 30, "start")
        refusal = page.locator(".notice.refusal .notice-text", has_text="Work cannot start after")
        await expect(refusal.first).to_be_visible(timeout=SLOW)
        date_words = await refusal.first.inner_text()
        proof(
            "M16",
            "the work is scheduled on the Roadmap, dates typed in the panel and a drop from the "
            "tray: the bars are the dates MCP reads, set by the person, ten days apart, with the "
            "dependency drawn as an arrow, and a start dragged past the target is refused in the "
            "server's words",
            all(bars_say)
            and all(by_whom)
            and on(scheduled[later]["start_on"]) - on(scheduled[task]["start_on"])
            == datetime.timedelta(10)
            and placed == {epic, story, task, later}
            and same_words(date_words, say_date)
            and (await ok(agent, "get_node", node_id=later))["start_on"] == node["start_on"],
            f"{scheduled[task]['start_on']} and {scheduled[later]['start_on']}",
        )
        assert w.problems == [], w.problems
