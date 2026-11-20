"""Chapter 8 of the acceptance scenario: the Board (M10, M14, M16 and M20), driven in a real
headless browser against the real containers, with what happens read back through a real MCP
client. Each proof is shown the way a person would see it: what was done in the browser, and what
the graph then holds. Nothing opens at the side of a page (M20): a card, a bar or a dot leads to
the full page of its record, whose path keeps the view the person came from. The last part is
M16's day of work, done with only Board, Graph and Roadmap as views (and the pages they lead to),
in a project of its own so that every number can be exact."""

import asyncio
import datetime
import re
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
    cell,
    create_in_browser,
    drag_bar,
    http,
    made,
    on,
    open_editor,
    open_item,
    roadmap_ids,
    timeline,
)
from tests.helpers import (
    code_of,
    connect,
    connect_as,
    day,
    embeddings_stopped,
    make_note,
    number_of,
    ok,
    refused,
    reindexed,
    the_token,
    unique_word,
)
from tests.world import make_project, register

ACTOR = "Ada Lovelace"


def same_words(board_words: str, mcp_words: str) -> bool:
    """The board said what the tool says, without the protocol's "Error executing tool"."""
    return bool(board_words.strip()) and mcp_words.endswith(board_words.strip())


async def back_to_view(page: Page, name: str) -> None:
    """Go back to the view the path names, as a person does with its crumb, and wait for it."""
    crumb = page.locator("nav.crumbs .crumb-view a")
    await expect(crumb).to_have_text(name, timeout=SLOW)
    await crumb.click()
    await expect(page.locator("main#page h1").first).to_have_text(name, timeout=SLOW)


async def on_the_board(page: Page) -> None:
    """The New item button is on the Board: a new item opens its own page, so after making one the
    person goes back to the Board with the crumb."""
    if await page.locator("#new-item").count() == 0:
        await back_to_view(page, "Board")


async def create_here(
    page: Page, kind: str, title: str, parent: str | None = None, *, body: str = ""
) -> str:
    """The Board's New item (create_in_browser), from wherever the person is: the page of the item
    that was made before it, say."""
    await on_the_board(page)
    return await create_in_browser(page, kind, title, parent, body=body)


async def board() -> None:
    chapter("The board: Board, Graph and Roadmap, for people next to the agents")
    page_url = board_url(URL)
    token = the_token()
    try:
        async with connect(URL, token) as client:
            await destinations(page_url, token)
            await agents_show_up(client, page_url, token)
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
            client, "add_link", actor="agent-1", source=second, target=first,
            link_type="needs_first",
        )  # fmt: skip
        await ok(client, "claim_node", actor="agent-2", node=first)
        await ok(
            client, "update_node", actor="agent-2", node=first, expected_version=1,
            status="in_progress",
        )  # fmt: skip
        started = time.monotonic()
        await expect(
            cell(w.page, epic, "ready").locator(f".card[data-ref='{story}']")
        ).to_be_visible(timeout=SLOW)
        await expect(
            cell(w.page, epic, "in_progress").locator(f".card[data-ref='{first}']")
        ).to_contain_text("Held by agent-2")
        # A card that waits says one word, Waiting, and what it waits for is in a hover card.
        chip = cell(w.page, epic, "new").locator(f".card[data-ref='{second}'] .card-waiting")
        await expect(chip).to_have_text("Waiting", timeout=SLOW)
        await chip.hover()
        await expect(w.page.locator(".hovercard:visible")).to_contain_text(
            f"{code_of(first)} Template {word}"
        )
        await w.page.mouse.move(0, 0)
        seconds = time.monotonic() - started
        proof(
            "M10",
            "an epic, story, task, link and claim made through MCP show on the board with no "
            "reload, in the right lane and column, with who holds it and what blocks it (a card "
            "that waits says Waiting, and the hover card names what it waits for)",
            seconds < 10 and await w.page.evaluate("window.__same") == marker,
            f"{seconds:.1f} seconds, the same page",
        )


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
        epic = await create_here(page, "epic", f"Portal {word}")
        story = await create_here(page, "story", f"Sign in {word}", epic)
        other = await create_here(page, "story", f"Profile {word}", epic)
        task = await create_here(page, "task", f"Login form {word}", story)
        friend = await create_here(page, "task", f"Logout {word}", story)
        await open_item(w, task)
        await expect(page.locator("#item-title")).to_contain_text(f"{code_of(task)} ")
        await open_editor(page)
        await page.locator("#edit-title").fill(f"Login form, better {word}")
        await page.locator("#edit-body").fill("Email and password, then a code.")
        await page.locator("#edit-priority").select_option("high")
        await page.get_by_role("button", name="Save changes").click()
        await expect(page.locator("#item-details .card-detail")).to_contain_text(
            "Email and password"
        )
        await w.go(f"#/board?epic={number_of(epic)}")
        await page.drag_and_drop(
            f".card[data-ref='{task}']", f".cell[data-epic='{epic}'][data-status='in_progress']"
        )
        await expect(
            cell(page, epic, "in_progress").locator(f".card[data-ref='{task}']")
        ).to_be_visible()
        await page.drag_and_drop(
            f".card[data-ref='{task}']", f".cell[data-epic='{epic}'][data-status='done']"
        )
        await expect(cell(page, epic, "done").locator(f".card[data-ref='{task}']")).to_be_visible()
        await page.locator(f".card[data-ref='{task}'] .card-title a").click()
        await expect(page.locator("#item-page")).to_have_attribute("data-ref", task, timeout=SLOW)
        await expect(page.locator("#item-page .card-summary .chips")).to_contain_text("Done")
        await page.locator("#move-parent").select_option(other)
        await page.get_by_role("button", name="Move", exact=True).click()
        await expect(page.locator("nav.crumbs li.crumb-item a").last).to_have_attribute(
            "title", f"{code_of(other)} Profile"
        )
        await page.locator("#link-relation").select_option("relates_to:out")
        await page.locator("#link-target").fill(code_of(friend))
        await page.get_by_role("button", name="Add", exact=True).click()
        await expect(page.locator("#item-page .remove-link")).to_have_count(1)
        await page.locator("#item-page .remove-link").click()
        await expect(page.locator("#item-page .remove-link")).to_have_count(0)
        assert w.problems == [], w.problems

    final = await ok(client, "get_node", node=task)
    events = (await ok(client, "node_history", node=task, limit=200))["items"]
    listed = [n["ref"] for n in (await ok(client, "list_nodes", parent=other))["items"]]
    links = (await ok(client, "list_links", node=task))["items"]
    proof(
        "M10",
        "what a person does in the browser (create, edit on the task's own page, drag on the "
        "Board to in progress and done, move and add and remove a link on its page) is what get, "
        "list and history then show, with their name and versions",
        (final["title"], final["body"], final["priority"], final["status"])
        == (f"Login form, better {word}", "Email and password, then a code.", "high", "done")
        and (final["parent"], final["version"]) == (other, 5)
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
    await ok(client, "add_link", actor="a", source=second, target=first, link_type="needs_first")
    say_ready = await refused(
        client, "update_node", actor="x", node=second, expected_version=1, status="ready"
    )
    say_cycle = await refused(
        client, "add_link", actor="x", source=first, target=second, link_type="needs_first"
    )
    say_blank = await refused(
        client, "update_node", actor="  ", node=story, expected_version=1, title="x"
    )
    say_dates = await refused(
        client, "update_node", actor="x", node=dated, expected_version=1, start_on=day(12)
    )
    before = (
        await ok(client, "get_node", node=second),
        await ok(client, "list_links", node=first),
        await ok(client, "get_node", node=dated),
    )
    async with browser_page(page_url, token=token) as w:
        page = w.page
        await w.open(f"#/board?epic={number_of(epic)}")
        await expect(cell(page, epic, "new").locator(f".card[data-ref='{second}']")).to_be_visible()
        await page.drag_and_drop(
            f".card[data-ref='{second}']", f".cell[data-epic='{epic}'][data-status='ready']"
        )
        ready_words = await page.locator(".notice.refusal .notice-text").first.inner_text()
        proof(
            "M10",
            "dropping a card on ready is refused with the words update_node gives, and the card "
            "stays",
            same_words(ready_words, say_ready),
            f'"{clip(ready_words, 80)}"',
        )
        await open_item(w, first)
        await page.locator("#link-relation").select_option("needs_first:out")
        await page.locator("#link-target").fill(code_of(second))
        await page.get_by_role("button", name="Add", exact=True).click()
        cycle = page.locator("#item-page form.add-link .problem")
        await expect(cycle).not_to_be_empty()
        cycle_words = await cycle.inner_text()
        proof(
            "M10",
            "a link that makes a cycle is refused with the words add_link gives, and what was "
            "typed stays",
            same_words(cycle_words, say_cycle)
            and await page.locator("#link-target").input_value() == code_of(second),
            f'"{clip(cycle_words, 80)}"',
        )
        await open_item(w, story)
        await open_editor(page)
        title = page.locator("#edit-title")
        await title.fill("My title")
        await ok(
            client, "update_node", actor="agent-1", node=story, expected_version=1,
            title="Agent title",
        )  # fmt: skip
        await expect(page.locator("#item-page .notice.changed")).to_be_visible(timeout=SLOW)
        say_stale = await refused(
            client, "update_node", actor="x", node=story, expected_version=1, title="y"
        )
        await page.get_by_role("button", name="Save changes").click()
        stale = page.locator("#item-page .edit .problem .refusal-text")
        await expect(stale).to_be_visible()
        stale_words = await stale.inner_text()
        kept = await title.input_value()
        await page.locator("#item-page .edit .problem .load-current").click()
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
        blank = page.locator("#item-page .edit .problem .refusal-text")
        await expect(blank).to_be_visible()
        blank_words = await blank.inner_text()
        proof(
            "M10",
            "a blank name is refused with the words update_node gives",
            same_words(blank_words, say_blank),
            f'"{clip(blank_words, 80)}"',
        )
        await page.evaluate("localStorage.setItem('vrum.actor', 'Tester')")
        await open_item(w, dated)
        await open_editor(page)
        await page.locator("#edit-start").fill(day(12))
        await page.get_by_role("button", name="Save changes").click()
        late = page.locator("#item-page .edit .problem .refusal-text")
        await expect(late).to_be_visible(timeout=SLOW)
        late_words = await late.inner_text()
        proof(
            "M1A",
            "a start date after the target typed on the item's page is refused with the words "
            "update_node gives, and what was typed stays",
            same_words(late_words, say_dates)
            and await page.locator("#edit-start").input_value() == day(12),
            f'"{clip(late_words, 80)}"',
        )
    after = (
        await ok(client, "get_node", node=second),
        await ok(client, "list_links", node=first),
        await ok(client, "get_node", node=dated),
    )
    proof(
        "M10",
        "none of the five refusals changed the graph",
        after == before and (await ok(client, "get_node", node=story))["title"] == "Agent title",
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
        await open_item(w, story)
        await open_editor(page)
        await page.locator("#edit-body").fill(f"The phrase {word} appears here")
        await page.get_by_role("button", name="Save changes").click()
        await expect(page.locator("#item-details .card-detail")).to_contain_text(
            f"The phrase {word}"
        )
        found = await ok(client, "search", query=word, mode="words")
        by_words = [i["ref"] for i in found["items"] if i["item_type"] == "story"] == [story]
        await settle(client)
        await expect(page.locator("#index-state")).to_be_hidden(timeout=SLOW)
        proof(
            "M10",
            "a story edited on the board is found by search at once, and the waiting count reaches "
            "zero with no manual reindex (the top bar's note about the index goes away: nothing "
            "waits)",
            by_words,
        )
        with embeddings_stopped():
            before = await waiting(client)
            await open_editor(page)
            await page.locator("#edit-body").fill(f"The second phrase {later} appears here")
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator("#item-details .card-detail")).to_contain_text(
                f"second phrase {later}"
            )
            saved = f"second phrase {later}" in (await ok(client, "get_node", node=story))["body"]
            one = await waiting(client) == before + 1
            await expect(page.locator("#index-state")).to_have_text(
                "Search by meaning is not answering: 1 waiting", timeout=SLOW
            )
        await settle(client)
        await expect(page.locator("#index-state")).to_be_hidden(timeout=SLOW)
        again = await ok(
            client, "search", query=f"Indexed story {word} The second phrase {later} appears here"
        )
        proof(
            "M10",
            "with the embedding service stopped the edit still saves and the board and search both "
            "say one item waits; when it is back the count reaches zero and the item is found by "
            "meaning",
            saved and one and {i["ref"]: i["matched"] for i in again["items"]}[story] == "both",
        )


# -- what is shown is only text, and it all comes from the server's own address -------------------


async def only_text(client: Client, page_url: str, token: str) -> None:
    word = unique_word()
    markup = f'<img src=x onerror="window.__ran=1"><script>window.__ran=2</script> {word}'
    epic = await made(client, "epic", markup, body="<b>bold</b> <script>window.__ran=3</script>")
    story = await made(client, "story", f"<i>{word}</i>", epic)
    note = await make_note(
        client, f'<img src=x onerror="window.__ran=4"> a note {word}',
        f"<b>the source</b> {word}", nodes=[story],
    )  # fmt: skip
    decided = await make_note(
        client, f"<u>A decision</u> {word}\n\n<script>window.__ran=5</script> what was decided",
        '<img src=x onerror="window.__ran=6"> why', decided_by="Ada", nodes=[story],
    )  # fmt: skip
    _, headers, _ = http("GET", page_url)
    scope = number_of(epic)
    async with browser_page(page_url, token=token) as w:
        page = w.page
        await w.open(f"#/board?epic={scope}")
        await expect(page.locator(f"section.lane[data-ref='{epic}']")).to_contain_text(markup)
        pages = 0

        async def nothing_came_alive() -> bool:
            """No markup became an element on this page and no script of it ran."""
            nonlocal pages
            pages += 1
            return (
                await page.evaluate("typeof window.__ran") == "undefined"
                and await page.locator("img[src='x'], script:not([src]), b, u").count() == 0
            )

        quiet = await nothing_came_alive()
        await open_item(w, epic)
        await expect(page.locator("#item-title")).to_contain_text(markup, timeout=SLOW)
        await expect(page.locator("#item-details .body-text")).to_contain_text(
            "<b>bold</b> <script>window.__ran=3</script>"
        )
        quiet = await nothing_came_alive() and quiet
        await open_item(w, f"note:{note['id']}")
        await expect(page.locator("#item-page [data-part=why]")).to_contain_text(
            f"<b>the source</b> {word}", timeout=SLOW
        )
        quiet = await nothing_came_alive() and quiet
        await open_item(w, f"note:{decided['id']}")
        await expect(page.locator("#item-page [data-part=why]")).to_contain_text(
            '<img src=x onerror="window.__ran=6"> why', timeout=SLOW
        )
        quiet = await nothing_came_alive() and quiet
        await w.go(f"#/search?search={word}")
        await expect(page.locator("#search-results tr.result")).to_have_count(4, timeout=SLOW)
        quiet = await nothing_came_alive() and quiet
        await w.go(f"#/graph?tab=around&focus={story}")
        await expect(page.locator(f".graph-around[data-focus='{story}']")).to_be_visible(
            timeout=SLOW
        )
        quiet = await nothing_came_alive() and quiet
        policy = headers["content-security-policy"]
        proof(
            "M10",
            "a title, body, note text and why with HTML and script are shown as literal text in "
            "the board, the pages of an epic and of two notes, the search page and the Graph and "
            "nothing runs; every request goes to the server's own address, and the content "
            "security policy is set",
            quiet
            and w.dialogs == []
            and all(u.startswith(w.origin) for u in w.requests)
            and "default-src 'none'" in policy
            and "'unsafe-inline'" not in policy,
            f"{pages} pages, {len(w.requests)} requests, all to {w.origin}",
        )
        assert w.problems == [], w.problems


# -- M16: a day's work with Board, Graph and Roadmap ------------------------------------------


async def record_ids(page: Locator, section: str) -> list[int]:
    """The ids of the notes in one section of the recall a record's page shows."""
    numbers: list[int] = await page.locator(
        f".recall-section[data-section={section}] tr.record-line"
    ).evaluate_all("els => els.map(e => Number(e.dataset.id))")
    return numbers


async def attempt_new(page: Page, kind: str, title: str) -> str:
    """Fill the New item form with no parent to choose from, and give what it says."""
    await on_the_board(page)
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
        connect_as("agent-keyed", project) as keyed,
        connect_as("agent-b", project) as bee,
        browser_page(project.address, token=project.key, name=ACTOR) as w,
    ):
        page = w.page
        here = page.locator("#item-page")

        # -- the Board: plan the work, in an empty project
        await w.open("")
        await expect(page.locator("h1")).to_have_text("Board", timeout=SLOW)
        await expect(page.locator("#board-empty")).to_be_visible(timeout=SLOW)
        said_story = await refused(agent, "create_node", kind="story", title="Orphan")
        story_words = await attempt_new(page, "story", f"Orphan story {word}")
        epic = await create_here(page, "epic", f"Billing {word}")
        said_task = await refused(agent, "create_node", kind="task", title="Orphan")
        task_words = await attempt_new(page, "task", f"Orphan task {word}")
        story = await create_here(page, "story", f"Invoices {word}", epic)
        task = await create_here(page, "task", f"Template {word}", story)
        later = await create_here(page, "task", f"Send them {word}", story)
        listed = {n["ref"]: n for n in (await ok(agent, "list_nodes", limit=200))["items"]}
        await back_to_view(page, "Board")
        for ref in (story, task, later):
            await expect(page.locator(f".card[data-ref='{ref}']")).to_have_attribute(
                "data-status", listed[ref]["status"]
            )
        await page.locator(f".card[data-ref='{story}'] .card-title a").click()
        await expect(page.locator("#item-title")).to_contain_text(
            f"{code_of(story)} ", timeout=SLOW
        )
        await page.locator("#item-page button.add-requirement").click()
        dialog = page.locator("dialog.modal")
        await expect(dialog.locator("#requirement-on")).to_contain_text(
            f"It goes on {code_of(story)}"
        )
        await dialog.locator("#requirement-title").fill(f"Every invoice shows its VAT {word}")
        await dialog.get_by_role("button", name="Add the acceptance criterion").click()
        # A new acceptance criterion is on the story's page at once, in its table.
        row = here.locator("[data-part=requirements] tr.record-line")
        await expect(row).to_have_count(1, timeout=SLOW)
        rule_id = int(await row.get_attribute("data-rule") or 0)
        rule = await ok(agent, "get_rule", rule_id=rule_id)
        history_of_rule = (await ok(agent, "rule_history", rule_id=rule_id))["items"]
        await row.locator("a.record-title").click()
        await expect(here).to_have_attribute("data-kind", "rule", timeout=SLOW)
        await expect(page.locator("#item-what")).to_contain_text(f"On {code_of(story)}")
        await page.locator("#item-what a").click()
        await expect(here).to_have_attribute("data-ref", story, timeout=SLOW)
        await expect(row).to_have_count(1, timeout=SLOW)
        in_the_table = int(await row.get_attribute("data-rule") or 0)
        await back_to_view(page, "Board")
        covered = await ok(agent, "coverage_report", show="both", limit=1)
        proof(
            "M16",
            "the day starts on the Board in an empty project: a story or task with no parent is "
            "refused in the tool's words, an epic, a story and two tasks made there are the ones "
            "MCP lists, numbered from 1 each kind on its own, and an acceptance criterion is added "
            "to the story from its page by the person, is in the story's table at once and has a "
            "page of its own (so no story is left without one)",
            same_words(story_words, said_story)
            and same_words(task_words, said_task)
            and set(listed) == {epic, story, task, later}
            and [listed[i]["kind"] for i in (epic, story, task, later)]
            == ["epic", "story", "task", "task"]
            and [listed[i]["number"] for i in (epic, story, task, later)] == [1, 1, 1, 2]
            and (listed[story]["parent"], listed[task]["parent"]) == (epic, story)
            and (rule["node"], rule["title"]) == (story, f"Every invoice shows its VAT {word}")
            and in_the_table == rule_id
            and covered["story_without_rules_count"] == 0
            and history_of_rule[0]["actor"] == ACTOR,
            f"{code_of(epic)}, {code_of(story)}, {code_of(task)} and {code_of(later)}, "
            f"acceptance criterion {rule_id}",
        )

        # -- what agents know: notes, then ready work claimed through MCP
        plain_note = await make_note(
            keyed, f"VAT is 19 percent {word}", "The tax office site", nodes=[task]
        )
        decided = await make_note(
            keyed, f"Round per line {word}", "Because", decided_by="Ada", nodes=[task]
        )
        wide = await make_note(keyed, f"Always in euros {word}", "Because", project_wide=True)
        taken = await ok(keyed, "next_work")
        briefing = taken["context"]
        await expect(page.locator(f".card[data-ref='{task}']")).to_contain_text(
            "Held by agent-keyed", timeout=SLOW
        )
        await page.locator("#activity-open").click()
        await expect(page.locator("#claims-live")).to_be_visible(timeout=SLOW)
        claim = page.locator(f"#claims-live .claim[data-node='{task}']")
        await expect(claim).to_have_attribute("data-actor", "agent-keyed", timeout=SLOW)
        await expect(claim).to_have_attribute("data-live", "1")
        taking = page.locator(
            "#feed li.event[data-action=claimed]", has=page.locator(f"a[data-ref='{task}']")
        )
        await expect(taking.first).to_contain_text("agent-keyed", timeout=SLOW)
        working = await ok(agent, "who_is_working")
        mine = next(a for a in working["agents"] if a["actor"] == "agent-keyed")
        # The claim leads to the task's own page, and the path says it was reached from Activity.
        await claim.locator("a.item-link").click()
        await expect(here).to_have_attribute("data-ref", task, timeout=SLOW)
        await expect(page.locator("nav.crumbs .crumb-view")).to_have_text("Activity")
        await expect(page.locator("#item-page .card-summary .claim")).to_contain_text(
            "Held by agent-keyed"
        )

        # -- the page holds the context the agent was briefed with, and the history
        await expect(here.locator("[data-section=notes] tr.record-line")).to_have_count(2)
        recalled = await ok(agent, "recall", node=task, limit=50)

        def held(section: str) -> list[int]:
            return [item["note"]["id"] for item in recalled[section]["items"]]

        shown = {
            section: await record_ids(here, section) for section in ("notes", "project_wide_notes")
        }
        note_row = here.locator(f"tr.record-line[data-ref='note:{plain_note['id']}']")
        await expect(note_row).to_have_attribute("data-status", "unconfirmed")
        await expect(note_row.locator("td.cell-title")).to_contain_text("The tax office site")
        await expect(
            here.locator(f"tr.record-line[data-ref='note:{decided['id']}'] td.cell-decided")
        ).to_contain_text("Ada")
        # What is linked here is shown; the project-wide notes that apply anyway are one
        # disclosure (open when it holds fewer than five), not a second set of tables.
        wide_open = (
            await here.locator("details[data-disclosure=project_wide_notes]").get_attribute("open")
            is not None
        )
        proof(
            "M16",
            "ready work claimed through MCP shows on its card, as a live claim on the Activity "
            "page, where who is working on what is the server's own list, and on the task's own "
            "page, reached from the claim with Activity in its path",
            taken["work"]["ref"] == task
            and [h["node"] for h in mine["holding"]] == [task]
            and [r["id"] for r in briefing["acceptance_criteria"]] == [rule_id],
            "held by agent-keyed",
        )
        proof(
            "M16",
            "the work's context is read on its page, as tables: its notes and every "
            "project-wide note are exactly what recall says and what the agent was briefed "
            "with, the plain note shown unconfirmed with its why, the decided one with who "
            "decided it, and the project-wide ones in one disclosure that is open while it holds "
            "fewer than five",
            shown["notes"]
            == held("notes")
            == [decided["id"], plain_note["id"]]
            == [n["id"] for n in briefing["notes"]]
            and shown["project_wide_notes"] == held("project_wide_notes")
            and wide["id"] in shown["project_wide_notes"]
            and wide["id"] in [n["id"] for n in briefing["project_wide_notes"]]
            and wide_open,
            f"{len(shown['notes'])} notes, {len(shown['project_wide_notes'])} project-wide",
        )

        # -- the handoff
        await ok(
            keyed, "finish_work", node=task, outcome="done",
            note=f"Built the template; VAT is a field, not a constant {word}.",
        )  # fmt: skip
        last = here.locator("[data-part=handoff]")
        await expect(last).to_contain_text(f"VAT is a field, not a constant {word}", timeout=SLOW)
        await expect(page.locator("#item-page .card-summary")).not_to_contain_text(
            "Held by", timeout=SLOW
        )
        events = (await ok(agent, "node_history", node=task, limit=200))["items"]
        on_page = await here.locator("[data-part=history] li").evaluate_all(
            "els => els.map(e => Number(e.dataset.event))"
        )
        await page.locator("#activity-open").click()
        handoff = page.locator(
            "#feed li.event[data-action=handoff]", has=page.locator(f"a[data-ref='{task}']")
        )
        await expect(handoff.first).to_contain_text("agent-keyed", timeout=SLOW)
        await expect(handoff.first).to_contain_text(f"VAT is a field, not a constant {word}")
        await expect(claim).to_have_count(0, timeout=SLOW)
        await page.locator("#main-nav a[data-page=board]").click()
        await expect(cell(page, epic, "done").locator(f".card[data-ref='{task}']")).to_be_visible(
            timeout=SLOW
        )
        proof(
            "M16",
            "after finish_work the handoff note is on the task's page and in the Activity feed in "
            "full, the claim is gone and the card is in Done, and the page's history is the "
            "item's history, newest first",
            {"create", "claimed", "handoff"} <= {e["action"] for e in events}
            and sorted(on_page) == sorted(e["id"] for e in events)
            and on_page == sorted(on_page, reverse=True)
            and (await ok(agent, "get_node", node=task))["status"] == "done",
            f"{len(events)} events",
        )

        # -- the Graph: look around the task, then the page of it from there
        await page.locator("#main-nav a[data-page=graph]").click()
        await expect(page.locator("#graph-explore")).to_be_visible(timeout=SLOW)
        await w.go(f"#/graph?tab=around&focus={task}")
        hub = page.locator("#graph-hub")
        await expect(hub).to_have_attribute("data-ref", task, timeout=SLOW)
        await expect(hub).to_have_class(re.compile(r"\bs-done\b"))
        for key in (f"note:{plain_note['id']}", f"note:{decided['id']}", f"rule:{rule_id}"):
            await expect(
                page.locator(f".graph-around button.graph-node[data-ref='{key}']")
            ).to_be_visible(timeout=SLOW)
        await expect(here).to_have_count(0)
        await page.locator("#graph-hub-open").click()
        await expect(here).to_have_attribute("data-ref", task, timeout=SLOW)
        from_the_graph = "from=graph" in page.url
        await back_to_view(page, "Graph")
        proof(
            "M16",
            "the Graph draws the same work and context: looking around the finished task shows "
            "it done, with its notes and its acceptance criterion drawn around it, Open page "
            "opens the task's page with the Graph in its path and the crumb brings the Graph "
            "back",
            from_the_graph,
        )

        # -- refusals, from the existing owners: a cycle, a stale edit, a claim, a date
        await ok(bee, "add_link", source=later, target=task, link_type="needs_first")
        say_cycle = await refused(
            agent, "add_link", source=task, target=later, link_type="needs_first"
        )
        before = {i: await ok(agent, "get_node", node=i) for i in (task, later)}
        await open_item(w, task)
        await page.locator("#link-relation").select_option("needs_first:out")
        await page.locator("#link-target").fill(code_of(later))
        await page.get_by_role("button", name="Add", exact=True).click()
        problem = page.locator("#item-page form.add-link .problem")
        await expect(problem).not_to_be_empty(timeout=SLOW)
        cycle_words = await problem.inner_text()
        link_kept = await page.locator("#link-target").input_value() == code_of(later)
        await open_item(w, later)
        await expect(page.locator("#item-title")).to_contain_text(
            f"{code_of(later)} ", timeout=SLOW
        )
        await open_editor(page)
        await page.locator("#edit-title").fill(f"My title {word}")
        version = (await ok(agent, "get_node", node=later))["version"]
        await ok(
            keyed, "update_node", node=later, expected_version=version,
            title=f"Agent title {word}",
        )  # fmt: skip
        say_stale = await refused(
            agent, "update_node", node=later, expected_version=version, title="x"
        )
        await page.get_by_role("button", name="Save changes").click()
        stale = page.locator("#item-page .edit .problem .refusal-text")
        await expect(stale).to_be_visible(timeout=SLOW)
        stale_words = await stale.inner_text()
        title_kept = await page.locator("#edit-title").input_value() == f"My title {word}"
        await ok(bee, "claim_node", node=later)
        say_claim = await refused(agent, "claim_node", node=later)
        await page.locator("#activity-open").click()
        await expect(page.locator("#claim-id")).to_be_visible(timeout=SLOW)
        await page.locator("#claim-id").fill(code_of(later))
        await page.locator("#claim-by-id-go").click()
        refusal = page.locator(".notice.refusal .notice-text").first
        await expect(refusal).to_be_visible(timeout=SLOW)
        claim_words = await refusal.inner_text()
        code_kept = await page.locator("#claim-id").input_value() == code_of(later)
        held_by = (await ok(agent, "get_node", node=later))["claimed_by"]
        await ok(bee, "release_claim", node=later, note="Not today.")
        proof(
            "M16",
            "a dependency cycle, a stale edit and a claim another agent holds are refused in the "
            "words of the tools that own them, and nothing the person typed is lost",
            same_words(cycle_words, say_cycle)
            and same_words(stale_words, say_stale)
            and same_words(claim_words, say_claim)
            and "agent-b" in say_claim
            and held_by == "agent-b"
            and (await ok(agent, "get_node", node=task))["version"] == before[task]["version"]
            and link_kept
            and title_kept
            and code_kept
            and (await ok(agent, "get_node", node=later))["title"] == f"Agent title {word}",
            f'"{clip(claim_words, 70)}"',
        )

        # -- the Roadmap: schedule the work, the dates typed on each task's page
        await page.locator("#main-nav a[data-page=roadmap]").click()
        await expect(page.locator("h1")).to_have_text("Roadmap", timeout=SLOW)
        await w.go(f"#/roadmap?q=epic:{number_of(epic)}")
        await expect(page.locator("#count")).to_contain_text(
            "0 items on the timeline", timeout=SLOW
        )
        today = (await timeline(page))["today"]
        first_start = today + datetime.timedelta(days=2)
        for ref, start in ((task, first_start), (later, first_start + datetime.timedelta(10))):
            await open_item(w, ref, "roadmap")
            await expect(here).to_have_attribute("data-ref", ref, timeout=SLOW)
            await expect(page.locator("nav.crumbs .crumb-view")).to_have_text("Roadmap")
            await open_editor(page)
            await page.locator("#edit-start").fill(start.isoformat())
            await page.locator("#edit-target").fill((start + datetime.timedelta(6)).isoformat())
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator("#item-page .card-summary .start")).to_contain_text(
                start.isoformat(), timeout=SLOW
            )
            # The crumb brings the Roadmap back as it was left, with its filter and the bar.
            await back_to_view(page, "Roadmap")
            await expect(page.locator("#filter")).to_have_value(f"epic:{number_of(epic)}")
            await expect(page.locator(f".rm-bar[data-ref='{ref}']")).to_be_visible(timeout=SLOW)
        scheduled = {i: await ok(agent, "get_node", node=i) for i in (task, later)}
        bars_say = []
        by_whom = []
        for ref in (task, later):
            bar = page.locator(f".rm-bar[data-ref='{ref}']")
            bars_say.append(
                (await bar.get_attribute("data-start"), await bar.get_attribute("data-end"))
                == (scheduled[ref]["start_on"], scheduled[ref]["target_on"])
            )
            changes = [
                e
                for e in (await ok(agent, "node_history", node=ref, limit=200))["items"]
                if e["action"] == "update" and "start_on" in e["after"]
            ]
            by_whom.append(
                [(e["actor"], e["before"]["start_on"]) for e in changes] == [(ACTOR, None)]
            )
        arrow = page.locator(f"path.dep[data-from='{task}'][data-to='{later}']")
        await expect(arrow).to_have_count(1)
        placed = await roadmap_ids(page, 4)
        node = scheduled[later]
        too_late = (on(node["start_on"]) + datetime.timedelta(days=8)).isoformat()
        say_date = await refused(
            agent, "update_node", node=later,
            expected_version=node["version"], start_on=too_late,
        )  # fmt: skip
        await drag_bar(page, later, 8, "start")
        refusal = page.locator(".notice.refusal .notice-text", has_text="Work cannot start after")
        await expect(refusal.first).to_be_visible(timeout=SLOW)
        date_words = await refusal.first.inner_text()
        proof(
            "M16",
            "the work is scheduled on the Roadmap, dates typed on each task's page (the Roadmap "
            "crumb brings the view back with its filter, and nothing without dates is drawn): the "
            "bars are the dates MCP reads, set by the person, ten days apart, with "
            "the dependency drawn as an arrow, and a start dragged past the target is refused in "
            "the server's words",
            all(bars_say)
            and all(by_whom)
            and on(scheduled[later]["start_on"]) - on(scheduled[task]["start_on"])
            == datetime.timedelta(10)
            and placed == {epic, story, task, later}
            and same_words(date_words, say_date)
            and (await ok(agent, "get_node", node=later))["start_on"] == node["start_on"],
            f"{scheduled[task]['start_on']} and {scheduled[later]['start_on']}",
        )
        assert w.problems == [], w.problems
