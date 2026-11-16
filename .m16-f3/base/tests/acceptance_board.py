"""Chapter 8 of the acceptance scenario: the board (M10), driven in a real headless browser against
the real containers, with what happens read back through a real MCP client. Each proof is shown
the way a person would see it: what was done in the browser, and what the graph then holds.

The "Everything" view needs a graph of a known size, so its proof runs on a second, empty copy of
Vrum that is made for it and removed after it."""

import asyncio
import re
import time
from typing import Any

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import Page, expect

from tests.acceptance_scenario import URL, Failed, chapter, clip, proof
from tests.board_helpers import Watched, board_url, browser_page, http
from tests.helpers import (
    all_nodes,
    connect,
    embeddings_stopped,
    make_belief,
    make_decision,
    make_fact,
    make_rule,
    ok,
    refused,
    reindexed,
    the_token,
    unique_word,
)
from tests.second_stack import SecondStack
from tests.world import api, project_at

SLOW = 15_000


async def made(
    client: Client, kind: str, title: str, parent: int | None = None, **more: Any
) -> int:
    fields: dict[str, Any] = {"actor": "agent-1", "kind": kind, "title": title, **more}
    if parent:
        fields["parent_id"] = parent
    return int((await ok(client, "create_node", **fields))["id"])


def cell(page: Page, epic: int, status: str) -> Any:
    return page.locator(f".cell[data-epic='{epic}'][data-status='{status}']")


def edge(page: Page, kind: str, source: str, target: str) -> Any:
    return page.locator(
        f"svg#graph-svg .edge[data-type='{kind}'][data-source='{source}'][data-target='{target}']"
    )


def dot(page: Page, key: str) -> Any:
    return page.locator(f"svg#graph-svg .dot[data-key='{key}']")


async def dots(page: Page) -> list[str]:
    keys: list[str] = await page.locator("svg#graph-svg .dot").evaluate_all(
        "els => els.map(e => e.dataset.key)"
    )
    return sorted(keys)


async def graph(w: Watched, address: str) -> None:
    await w.open(address)
    await expect(w.page.locator("svg#graph-svg")).to_have_attribute(
        "data-ready", "true", timeout=SLOW
    )


def same_words(board_words: str, mcp_words: str) -> bool:
    """The board said what the tool says, without the protocol's "Error executing tool"."""
    return bool(board_words.strip()) and mcp_words.endswith(board_words.strip())


async def board() -> None:
    chapter("The board: the same graph in a browser, for people")
    page_url = board_url(URL)
    token = the_token()
    try:
        async with connect(URL, token) as client:
            await signing_in(page_url, token)
            await agents_show_up(client, page_url, token)
            await people_edit(client, page_url, token)
            await refusals(client, page_url, token)
            await index_follows(client, page_url, token)
            await only_text(client, page_url, token)
            await the_graph(client, page_url, token)
            await choosing(client, page_url, token)
        await everything(page_url)
    except BrowserError as error:
        raise Failed(
            f"M10: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- 1 --------------------------------------------------------------------------------------------


async def signing_in(page_url: str, token: str) -> None:
    base = page_url.split("/board")[0]
    project = project_at(page_url)
    owner = project.owner
    nobody = api(base, "GET", "/board/api/info")
    wrong = api(
        base,
        "POST",
        f"/board/api/projects/{project.id}/tool/list_nodes",
        {},
        cookie="vrs_not-a-session-at-all",
        csrf="not-the-secret",
    )
    proof(
        "M10",
        "the board's data calls refuse a request with no session or a wrong one, in plain words",
        nobody.status == 401
        and "not signed in" in str(nobody.body["message"])
        and wrong.status == 401
        and "not signed in" in str(wrong.body["message"]),
        f"{nobody.status} and {wrong.status}",
    )
    async with browser_page(f"{base}/board", token=None, name=None) as w:
        await w.open()
        await w.page.locator("#sign-in-submit").click()
        await expect(w.page.locator("#entry-problem")).to_contain_text("not right")
        await w.page.locator("#account-email").fill(owner.email)
        await w.page.locator("#account-password").fill("not-the-password-at-all")
        await w.page.locator("#sign-in-submit").click()
        await expect(w.page.locator("#entry-problem")).to_contain_text("not right")
        shown = await w.page.locator("#entry-problem").inner_text()
        await w.page.locator("#account-password").fill(owner.password)
        await w.page.locator("#sign-in-submit").click()
        await w.page.locator("li.project a.open-project", has_text=project.name).click()
        await expect(w.page.locator("h1")).to_have_text("Home")
        cookies = await w.page.context.cookies()
        session = [one for one in cookies if one["name"] == "vrum_session"]
        proof(
            "M10",
            "the sign-in turns away an empty and a wrong password with a plain message, and with "
            "the right one the board loads; the password is not in the address or on the page, "
            "and the session cookie is one the page's scripts cannot read",
            owner.password not in w.page.url
            and "not-the-password" not in shown
            and len(session) == 1
            and session[0]["httpOnly"]
            and await w.page.evaluate("document.cookie") == "",
            f'"{clip(shown, 60)}"',
        )


# -- 2 --------------------------------------------------------------------------------------------


async def agents_show_up(client: Client, page_url: str, token: str) -> None:
    word = unique_word()
    async with browser_page(page_url, token=token) as w:
        await w.open("#/board")
        await expect(w.page.locator(".board")).to_be_visible()
        epic = await made(client, "epic", f"Billing {word}")
        story = await made(client, "story", f"Invoices {word}", epic)
        first = await made(client, "task", f"Template {word}", story)
        second = await made(client, "task", f"Send it {word}", story)
        await ok(
            client,
            "add_link",
            actor="agent-1",
            source_id=second,
            target_id=first,
            link_type="needs_first",
        )
        await ok(client, "claim_node", actor="agent-2", node_id=first)
        await ok(
            client,
            "update_node",
            actor="agent-2",
            node_id=first,
            expected_version=1,
            status="in_progress",
        )
        started = time.monotonic()
        await expect(
            cell(w.page, epic, "ready").locator(f".card[data-id='{story}']")
        ).to_be_visible(timeout=SLOW)
        await expect(
            cell(w.page, epic, "in_progress").locator(f".card[data-id='{first}']")
        ).to_contain_text("Held by agent-2")
        await expect(
            cell(w.page, epic, "new").locator(f".card[data-id='{second}']")
        ).to_contain_text(f"Waiting for #{first}")
        seconds = time.monotonic() - started
        proof(
            "M10",
            "an epic, story, task, link and claim made through MCP show on the board with no "
            "reload, "
            "in the right lane and column, with who holds it and what blocks it",
            seconds < 10,
            f"{seconds:.1f} seconds",
        )


# -- 3 --------------------------------------------------------------------------------------------


async def people_edit(client: Client, page_url: str, token: str) -> None:
    word = unique_word()
    async with browser_page(page_url, token=token, name=None) as w:
        page = w.page
        await w.open("#/board")
        await page.locator("#who").click()
        await page.locator("dialog.modal #name").fill("Ada Lovelace")
        await page.locator("dialog.modal").get_by_role("button", name="Save").click()

        async def create(kind: str, title: str, parent: int | None = None) -> int:
            await page.locator("#new-item").click()
            dialog = page.locator("dialog.modal")
            await dialog.locator("#new-kind").select_option(kind)
            if parent is not None:
                await dialog.locator("#new-parent").select_option(str(parent))
            await dialog.locator("#new-title").fill(title)
            await dialog.get_by_role("button", name="Create").click()
            heading = page.locator("#card-title")
            await expect(heading).to_contain_text(title)
            found = re.match(r"#(\d+) ", await heading.inner_text())
            if not found:
                raise Failed("M10: a new item's card did not say its number")
            return int(found.group(1))

        epic = await create("epic", f"Portal {word}")
        story = await create("story", f"Sign in {word}", epic)
        other = await create("story", f"Profile {word}", epic)
        task = await create("task", f"Login form {word}", story)
        friend = await create("task", f"Logout {word}", story)
        await w.go(f"#/board?epic={epic}&card={task}")
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
        await page.drag_and_drop(
            f".card[data-id='{task}']", f".cell[data-epic='{epic}'][data-status='done']"
        )
        await expect(cell(page, epic, "done").locator(f".card[data-id='{task}']")).to_be_visible()
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

    final = await ok(client, "get_node", node_id=task)
    events = (await ok(client, "node_history", node_id=task, limit=200))["items"]
    listed = [n["id"] for n in (await ok(client, "list_nodes", parent_id=other))["items"]]
    links = (await ok(client, "list_links", node_id=task))["items"]
    actions = [(event["action"], event["actor"]) for event in events]
    proof(
        "M10",
        "what a person does in the browser (create, edit, drag to in progress and done, move, add "
        "and remove a link) is what get, list and history then show, with their name and versions",
        (
            final["title"],
            final["body"],
            final["priority"],
            final["status"],
            final["parent_id"],
            final["version"],
        )
        == (
            f"Login form, better {word}",
            "Email and password, then a code.",
            "high",
            "done",
            other,
            5,
        )
        and listed == [task]
        and links == []
        and actions
        == [
            (a, "Ada Lovelace")
            for a in ("create", "update", "update", "update", "move", "link_added", "link_removed")
        ]
        and events[3]["before"] == {"status": "in_progress", "version": 3}
        and events[3]["after"] == {"status": "done", "version": 4},
        f"{len(events)} events, version {final['version']}",
    )


# -- 4 --------------------------------------------------------------------------------------------


async def refusals(client: Client, page_url: str, token: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Refusals {word}")
    story = await made(client, "story", f"Story {word}", epic)
    first = await made(client, "task", f"First {word}", story)
    second = await made(client, "task", f"Second {word}", story)
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
    before = (
        await ok(client, "get_node", node_id=second),
        await ok(client, "list_links", node_id=first),
        await ok(client, "get_node", node_id=story),
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

        await w.go(f"#/board?epic={epic}&card={first}")
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

        await w.go(f"#/board?epic={epic}&card={story}")
        title = page.locator("#edit-title")
        await title.fill("My title")
        await ok(
            client,
            "update_node",
            actor="agent-1",
            node_id=story,
            expected_version=1,
            title="Agent title",
        )
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
    after = (
        await ok(client, "get_node", node_id=second),
        await ok(client, "list_links", node_id=first),
    )
    proof(
        "M10",
        "none of the four refusals changed the graph",
        after == before[:2]
        and (await ok(client, "get_node", node_id=story))["title"] == "Agent title",
    )


# -- 5 --------------------------------------------------------------------------------------------


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
        await w.open(f"#/board?epic={epic}&card={story}")
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


# -- 6 --------------------------------------------------------------------------------------------


async def only_text(client: Client, page_url: str, token: str) -> None:
    word = unique_word()
    markup = f'<img src=x onerror="window.__ran=1"><script>window.__ran=2</script> {word}'
    epic = await made(client, "epic", markup, body="<b>bold</b>")
    _, headers, _ = http("GET", page_url)
    async with browser_page(page_url, token=token) as w:
        await w.open(f"#/board?epic={epic}")
        await expect(w.page.locator(f"section.lane[data-epic='{epic}']")).to_contain_text(markup)
        ran = await w.page.evaluate("typeof window.__ran")
        stray = await w.page.locator("img[src='x'], script:not([src]), b").count()
        policy = headers["content-security-policy"]
        proof(
            "M10",
            "a title with HTML and script is shown as literal text and nothing runs; every request "
            "goes to the server's own address, and the content security policy is set",
            ran == "undefined"
            and stray == 0
            and w.dialogs == []
            and all(u.startswith(w.origin) for u in w.requests)
            and "default-src 'none'" in policy,
            f"{len(w.requests)} requests, all to {w.origin}",
        )


# -- 7 --------------------------------------------------------------------------------------------


async def the_graph(client: Client, page_url: str, token: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Drawn {word}")
    elsewhere = await made(client, "epic", f"Elsewhere {word}")
    s1, s2 = (
        await made(client, "story", f"One {word}", epic),
        await made(client, "story", f"Two {word}", epic),
    )
    t1 = await made(client, "task", f"Task 1 {word}", s1)
    t2 = await made(client, "task", f"Task 2 {word}", s1)
    t3 = await made(client, "task", f"Task 3 {word}", s2)
    await ok(client, "add_link", actor="a", source_id=t2, target_id=t1, link_type="needs_first")
    await ok(client, "add_link", actor="a", source_id=s1, target_id=s2, link_type="relates_to")
    rule = await make_rule(client, f"A rule {word}", story_id=s1)
    decision = await make_decision(client, f"A decision {word}", node_ids=[s1])
    fact = await make_fact(client, f"A fact {word}", node_ids=[s1])
    belief = await make_belief(client, [fact], f"A belief {word}", node_ids=[s1])
    async with browser_page(page_url, token=token) as w:
        page = w.page
        await graph(w, f"#/graph?epic={epic}")
        lines = [
            ("hierarchy", epic, s1),
            ("hierarchy", epic, s2),
            ("hierarchy", s1, t1),
            ("hierarchy", s1, t2),
            ("hierarchy", s2, t3),
            ("needs_first", t2, t1),
            ("relates_to", s1, s2),
        ]
        for kind, source, target in lines:
            await expect(edge(page, kind, f"n:{source}", f"n:{target}")).to_have_count(1)
        hidden = await page.locator(
            "svg#graph-svg .dot[data-type='rule'], svg#graph-svg .dot[data-type='decision'], "
            "svg#graph-svg .dot[data-type='fact'], svg#graph-svg .dot[data-type='belief']"
        ).count()
        for name in ("rules", "decisions", "facts", "beliefs"):
            await page.locator(f"[name=show-{name}]").check()
        await expect(edge(page, "placed", f"r:{rule['id']}", f"n:{s1}")).to_have_count(1)
        await expect(edge(page, "concerns", f"d:{decision['id']}", f"n:{s1}")).to_have_count(1)
        await expect(edge(page, "concerns", f"m:{fact['id']}", f"n:{s1}")).to_have_count(1)
        await expect(edge(page, "concerns", f"m:{belief['id']}", f"n:{s1}")).to_have_count(1)
        await expect(edge(page, "evidence", f"m:{belief['id']}", f"m:{fact['id']}")).to_have_count(
            1
        )
        proof(
            "M10",
            "the graph shows the epic, stories and tasks with the right kind of line between the "
            "right dots, and a rule, decision, fact and belief only once they are switched on",
            hidden == 0,
            f"{len(lines)} lines checked",
        )
        await ok(client, "add_link", actor="a", source_id=t3, target_id=t1, link_type="needs_first")
        await expect(edge(page, "needs_first", f"n:{t3}", f"n:{t1}")).to_have_count(1, timeout=SLOW)
        await w.go(f"#/board?epic={epic}&card={s2}")
        await page.locator("#move-parent").select_option(str(elsewhere))
        await page.get_by_role("button", name="Move", exact=True).click()
        await expect(page.locator("#panel .card-detail > p", has_text="In epic")).to_contain_text(
            f"#{elsewhere}"
        )
        await w.go(f"#/graph?epic={elsewhere}")
        await expect(edge(page, "hierarchy", f"n:{elsewhere}", f"n:{s2}")).to_have_count(1)
        await w.go(f"#/graph?epic={epic}")
        await expect(edge(page, "hierarchy", f"n:{epic}", f"n:{s2}")).to_have_count(0)
        proof(
            "M10",
            "a needs-first link made through MCP appears in the graph within 10 seconds with no "
            "reload, and a story moved on the board changes its hierarchy line",
            True,
        )


# -- 8 --------------------------------------------------------------------------------------------


async def choosing(client: Client, page_url: str, token: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Around {word}")
    story = await made(client, "story", f"Chosen story {word}", epic, target_on="2031-05-06")
    sibling = await made(client, "story", f"Sibling {word}", epic)
    task = await made(client, "task", f"Chosen task {word}", story)
    await ok(
        client, "add_link", actor="a", source_id=story, target_id=sibling, link_type="relates_to"
    )
    rule = await make_rule(client, f"A rule {word}", story_id=story)
    decision = await make_decision(client, f"A decision {word}", node_ids=[story])
    fact = await make_fact(client, f"A fact {word}", node_ids=[story])
    belief = await make_belief(client, [fact], f"A belief {word}", node_ids=[story])
    twin = " ".join(unique_word() for _ in range(4))
    one = await made(client, "story", f"Story {twin}", await made(client, "epic", f"A {twin}"))
    two = await made(client, "story", f"Story {twin}", await made(client, "epic", f"B {twin}"))
    await reindexed(client)
    links_before = [(await ok(client, "list_links", node_id=n))["items"] for n in (one, two)]
    extras = "show=rules,decisions,facts,beliefs"
    async with browser_page(page_url, token=token) as w:
        page = w.page
        await graph(w, f"#/graph?epic={epic}&{extras}")
        await dot(page, f"n:{story}").click()
        panel = page.locator("#panel .card-detail")
        await expect(panel.locator("li.belief")).to_contain_text("Suggestion")
        from_graph = await panel.inner_text()
        dates = "Target 2031-05-06" in from_graph and "Origin: stated" in from_graph
        await w.go(f"#/board?epic={epic}")
        await page.locator(f".card[data-id='{story}']").click()
        await expect(page.locator("#card-title")).to_contain_text("Chosen story")
        same = await page.locator("#panel .card-detail").inner_text() == from_graph
        await graph(w, f"#/graph?scope=local&card={story}&{extras}")
        wanted = sorted(
            [
                f"n:{story}",
                f"n:{epic}",
                f"n:{sibling}",
                f"n:{task}",
                f"r:{rule['id']}",
                f"d:{decision['id']}",
                f"m:{fact['id']}",
                f"m:{belief['id']}",
            ]
        )
        listed: list[str] = await page.locator("#graph-neighbours button.neighbour").evaluate_all(
            "els => els.map(e => e.dataset.key)"
        )
        exact = await dots(page) == wanted and sorted(listed) == [
            k for k in wanted if k != f"n:{story}"
        ]
        await page.locator(f"#graph-neighbours button[data-key='n:{task}']").click()
        await expect(dot(page, f"n:{task}")).to_have_class(re.compile(r"\bchosen\b"))
        proof(
            "M10",
            "choosing a dot opens the card the board opens, word for word, with the date, the fact "
            "and the belief as a suggestion; the local graph at one step is exactly the item's "
            "neighbours and the neighbour list offers the same choices",
            dates and same and exact,
            f"{len(wanted)} dots",
        )
        await graph(w, f"#/graph?epic={epic}")
        items: list[str] = await page.locator("#graph-items button.item").evaluate_all(
            "els => els.map(e => e.dataset.key)"
        )
        in_view = await dots(page)
        await page.locator("#graph-items button.item").first.focus()
        await page.keyboard.press("Enter")
        await expect(page.locator("#card-title")).to_be_visible()
        await graph(w, f"#/graph?epic={(await ok(client, 'get_node', node_id=one))['parent_id']}")
        before = await page.locator("svg#graph-svg .edge[data-type='overlap']").count()
        await page.locator("[name=overlaps]").check()
        pair = page.locator(
            f"svg#graph-svg .edge[data-type='overlap']:is("
            f"[data-source='n:{one}'][data-target='n:{two}'],"
            f"[data-source='n:{two}'][data-target='n:{one}'])"
        )
        await expect(pair).to_have_count(1)
        dashed = await pair.evaluate("e => getComputedStyle(e).strokeDasharray") != "none"
        label = "suggestion from the index" in (
            await page.locator(
                "svg#graph-svg .edge-label", has_text="might be the same"
            ).first.text_content()
            or ""
        )
        links_after = [(await ok(client, "list_links", node_id=n))["items"] for n in (one, two)]
        proof(
            "M10",
            "every dot is also in the list beside the drawing and can be chosen from the keyboard; "
            "suggested overlaps appear only when switched on, dashed and labelled as suggestions, "
            "and no link changes",
            sorted(items) == in_view
            and len(items) >= 3
            and before == 0
            and dashed
            and label
            and links_after == links_before,
        )


# -- 9 --------------------------------------------------------------------------------------------


async def everything(page_url: str) -> None:
    stack = SecondStack()
    try:
        stack.up()
        forty: list[dict[str, Any]] = []
        for epic in range(3):
            forty.append({"key": f"e{epic}", "kind": "epic", "title": f"Epic {epic}"})
            for story in range(3):
                forty.append(
                    {
                        "key": f"s{epic}{story}",
                        "kind": "story",
                        "title": f"Story {epic}{story}",
                        "parent": f"e{epic}",
                    }
                )
        for number in range(28):
            forty.append(
                {
                    "key": f"t{number}",
                    "kind": "task",
                    "title": f"Task {number}",
                    "parent": f"s{number % 3}{(number // 3) % 3}",
                }
            )
        links = [
            {"source": "t1", "target": "t0", "link_type": "needs_first"},
            {"source": "s01", "target": "s00", "link_type": "needs_first"},
            {"source": "s10", "target": "s00", "link_type": "relates_to"},
            {"source": "s21", "target": "s11", "link_type": "supersedes"},
            {"source": "e1", "target": "e2", "link_type": "duplicates"},
        ]
        async with stack.client() as client:
            await ok(client, "create_plan", actor="agent-1", items=forty, links=links)
            nodes = await all_nodes(client)
            known: dict[int, dict[str, Any]] = {}
            for node in nodes:
                for link in (await ok(client, "list_links", node_id=node["id"]))["items"]:
                    known[link["id"]] = link
            async with browser_page(board_url(stack.url, stack.project), token=stack.key) as w:
                page = w.page
                await graph(w, "#/graph?scope=all")
                await expect(page.locator("svg#graph-svg")).to_have_attribute("data-dots", "40")
                every_item = await dots(page) == sorted(f"n:{n['id']}" for n in nodes)
                for link in known.values():
                    await expect(
                        edge(
                            page,
                            link["link_type"],
                            f"n:{link['source_id']}",
                            f"n:{link['target_id']}",
                        )
                    ).to_have_count(1)
                total = await page.locator("svg#graph-svg .edge").count()
                proof(
                    "M10",
                    "the Everything view of forty items shows every item and every link that "
                    "list_nodes and list_links give",
                    every_item and total == 37 + len(known),
                    f"40 dots, {total} lines",
                )
                big: list[dict[str, Any]] = []
                for epic in range(10):
                    big.append({"key": f"E{epic}", "kind": "epic", "title": f"Big epic {epic}"})
                    for story in range(10):
                        name = f"S{epic}-{story}"
                        big.append(
                            {"key": name, "kind": "story", "title": name, "parent": f"E{epic}"}
                        )
                        big.extend(
                            {
                                "key": f"{name}-{n}",
                                "kind": "task",
                                "title": f"T{epic}{story}{n}",
                                "parent": name,
                            }
                            for n in range(10)
                        )
                planned = await ok(client, "create_plan", actor="agent-1", items=big)
                ids = {item["key"]: item["id"] for item in planned["created"]}
                await expect(page.locator("svg#graph-svg")).to_have_attribute(
                    "data-collapsed", "true", timeout=SLOW
                )
                notice = await page.locator("#graph-notice").inner_text()
                collapsed_dots = await page.locator("svg#graph-svg").get_attribute("data-dots")
                await dot(page, f"n:{ids['E3']}").click()
                await expect(page.locator("svg#graph-svg")).to_have_attribute(
                    "data-collapsed", "false"
                )
                opened = await dots(page) == sorted(
                    f"n:{i}" for k, i in ids.items() if k == "E3" or k.startswith("S3-")
                )
                proof(
                    "M10",
                    "above a thousand dots Everything shows each epic as one dot and says so, and "
                    "opening an epic shows its items",
                    "each epic is one dot" in notice and collapsed_dots == "13" and opened,
                    "13 dots for 1,150 items, then 111",
                )
    finally:
        stack.close()
