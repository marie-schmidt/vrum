"""M10 against the running containers: the board's Kanban pages, driven in a real headless browser
and checked against a real MCP client (see ./check.sh). What a person does on the board is checked
by reading the graph back through the tools; what an agent does through the tools is checked by
looking at the board."""

import asyncio
import re
import time
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Locator, expect

from tests.board_helpers import Watched, board_url, browser_page, http
from tests.helpers import (
    embeddings_stopped,
    make_fact,
    make_rule,
    ok,
    refused,
    reindexed,
    the_token,
    unique_word,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SLOW = 15_000  # what the board may take to show an agent's work: the poll is every 5 seconds


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


def card(w: Watched, node_id: int) -> Locator:
    return w.page.locator(f".card[data-id='{node_id}']")


def cell(w: Watched, epic_id: int, status: str) -> Locator:
    return w.page.locator(f".cell[data-epic='{epic_id}'][data-status='{status}']")


def plain(board_words: str, mcp_words: str) -> None:
    """The board said what MCP says for the same attempt, without the protocol's prefix."""
    assert board_words.strip(), "the board said nothing"
    assert not board_words.startswith("Error executing tool")
    assert mcp_words.endswith(board_words.strip()), (board_words, mcp_words)


async def made(
    client: Client, kind: str, title: str, parent: int | None = None, **more: Any
) -> int:
    node = await ok(
        client,
        "create_node",
        actor="agent-1",
        kind=kind,
        title=title,
        **({"parent_id": parent} if parent else {}),
        **more,
    )
    return int(node["id"])


async def node(client: Client, node_id: int) -> dict[str, Any]:
    return await ok(client, "get_node", node_id=node_id)


async def history(client: Client, node_id: int) -> list[dict[str, Any]]:
    return list((await ok(client, "node_history", node_id=node_id, limit=200))["items"])


# -- 1. signing in ---------------------------------------------------------------------------------


async def test_the_board_turns_away_a_wrong_or_empty_token_and_its_data_calls_need_the_token(
    board: str,
) -> None:
    token = the_token()
    wrong = "a-wrong-token-" + unique_word()

    # The page files hold no data, so they are open.
    status, headers, text = http("GET", board)
    assert status == 200 and "Vrum board" in text
    assert token not in text
    # Every data call needs the token, and says so in plain words.
    for method, path, body in (
        ("GET", "/api/info", None),
        ("GET", "/api/index", None),
        ("POST", "/api/tool/list_nodes", {}),
        ("POST", "/api/tool/update_node", {"actor": "x", "node_id": 1}),
    ):
        status, _, text = http(method, board + path, body=body)
        assert status == 401 and "needs a token and none was sent" in text, (path, text)
        status, _, text = http(method, board + path, token=wrong, body=body)
        assert status == 401 and "The token is not right" in text and wrong not in text, path
    status, _, text = http("GET", board + "/api/info", token=token)
    assert status == 200 and "poll_seconds" in text

    async with browser_page(board, token=None, name=None) as w:
        await w.open()
        sign_in = w.page.locator("dialog.modal")
        await expect(sign_in).to_contain_text("Sign in")
        await sign_in.get_by_role("button", name="Sign in").click()
        await expect(sign_in.locator(".problem")).to_contain_text("needs a token and none was sent")
        await sign_in.locator("#token").fill(wrong)
        await sign_in.get_by_role("button", name="Sign in").click()
        await expect(sign_in.locator(".problem")).to_contain_text("The token is not right")
        assert wrong not in await sign_in.locator(".problem").inner_text()
        assert await w.page.locator(".board").count() == 0

        await sign_in.locator("#token").fill(token)
        await sign_in.get_by_role("button", name="Sign in").click()
        name = w.page.locator("dialog.modal #name")
        await expect(name).to_be_visible()
        await name.fill("Ada Lovelace")
        await w.page.locator("dialog.modal").get_by_role("button", name="Save").click()
        # Home is the first page after signing in.
        await expect(w.page.locator("h1")).to_have_text("Home")
        await expect(w.page.locator("#who")).to_contain_text("Ada Lovelace")

        # The token is kept for this tab only: not in the address, a cookie or the page's storage.
        assert token not in w.page.url
        assert await w.page.context.cookies() == []
        assert token not in await w.page.evaluate("JSON.stringify(localStorage)")
        assert all(token not in url for url in w.requests)


# -- 2. an agent's work shows up -----------------------------------


async def test_what_agents_do_through_mcp_shows_on_the_board_within_ten_seconds_with_no_reload(
    client: Client, board: str
) -> None:
    word = unique_word()
    async with browser_page(board, token=the_token()) as w:
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
        lane = w.page.locator(f"section.lane[data-epic='{epic}']")
        await expect(lane).to_be_visible(timeout=SLOW)
        await expect(cell(w, epic, "ready").locator(f".card[data-id='{story}']")).to_be_visible()
        working = cell(w, epic, "in_progress").locator(f".card[data-id='{first}']")
        await expect(working).to_contain_text("Held by agent-2")
        waiting = cell(w, epic, "new").locator(f".card[data-id='{second}']")
        await expect(waiting).to_contain_text(f"Waiting for #{first} Template {word}")
        assert time.monotonic() - started < 10 + 2

        # It is the same page: nothing was loaded again.
        assert await w.page.evaluate("window.__same") == marker
        # The epic lane and the story card show progress, as get_node and list_nodes do.
        await expect(lane).to_contain_text("0 of 2 tasks done")
        assert w.problems == [], w.problems


async def test_progress_counts_the_tasks_that_are_done_out_of_those_not_dropped(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Counted {word}")
    story = await made(client, "story", f"Counted story {word}", epic)
    other = await made(client, "story", f"Empty story {word}", epic)
    done = await made(client, "task", f"Task 0 {word}", story)
    open_ = await made(client, "task", f"Task 1 {word}", story)
    dropped = await made(client, "task", f"Task 2 {word}", story)
    await ok(client, "update_node", actor="a", node_id=done, expected_version=1, status="done")
    await ok(
        client, "update_node", actor="a", node_id=dropped, expected_version=1, status="dropped"
    )

    by_id = {item["id"]: item for item in (await ok(client, "list_nodes", parent_id=epic))["items"]}
    assert (await node(client, story))["progress_done"] == by_id[story]["progress_done"] == 1
    assert (await node(client, story))["progress_total"] == by_id[story]["progress_total"] == 2
    assert (await node(client, epic))["progress_done"] == 1
    assert (await node(client, epic))["progress_total"] == 2
    assert (await node(client, other))["progress_total"] == 0
    assert (await node(client, open_))["progress_done"] is None

    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}")
        await expect(w.page.locator(f"section.lane[data-epic='{epic}']")).to_contain_text(
            "1 of 2 tasks done"
        )
        await expect(card(w, story)).to_contain_text("1 of 2 tasks done")
        await expect(card(w, other)).to_contain_text("0 of 0 tasks done")
        assert w.problems == [], w.problems


# -- 3. what a person does on the board is what the graph then holds -------------------------------


async def test_what_a_person_does_on_the_board_is_in_the_graph_with_their_name_and_the_versions(
    client: Client, board: str
) -> None:
    word = unique_word()
    async with browser_page(board, token=the_token(), name=None) as w:
        await w.open("#/board")
        who = w.page.locator("dialog.modal #name")
        await expect(who).to_be_visible()
        await who.fill("Ada Lovelace")
        await w.page.locator("dialog.modal").get_by_role("button", name="Save").click()

        async def create(kind: str, title: str, parent: int | None = None) -> int:
            await w.page.locator("#new-item").click()
            dialog = w.page.locator("dialog.modal")
            await dialog.locator("#new-kind").select_option(kind)
            if parent is not None:
                await dialog.locator("#new-parent").select_option(str(parent))
            await dialog.locator("#new-title").fill(title)
            await dialog.get_by_role("button", name="Create").click()
            heading = w.page.locator("#card-title")
            await expect(heading).to_contain_text(title)
            found = re.match(r"#(\d+) ", await heading.inner_text())
            assert found
            return int(found.group(1))

        epic = await create("epic", f"Portal {word}")
        story = await create("story", f"Sign in {word}", epic)
        other_story = await create("story", f"Profile {word}", epic)
        task = await create("task", f"Login form {word}", story)
        friend = await create("task", f"Logout {word}", story)

        await w.go(f"#/board?epic={epic}&card={task}")
        await expect(w.page.locator("#card-title")).to_contain_text(f"#{task} ")
        await w.page.locator("#edit-title").fill(f"Login form, better {word}")
        await w.page.locator("#edit-body").fill("Email and password, then a code.")
        await w.page.locator("#edit-priority").select_option("high")
        await w.page.get_by_role("button", name="Save changes").click()
        await expect(w.page.locator("#panel .card-detail")).to_contain_text("Email and password")

        # Drag it to in progress, then to done: the same two changes an agent would make.
        await w.page.drag_and_drop(
            f".card[data-id='{task}']", f".cell[data-epic='{epic}'][data-status='in_progress']"
        )
        await expect(
            cell(w, epic, "in_progress").locator(f".card[data-id='{task}']")
        ).to_be_visible()
        await w.page.drag_and_drop(
            f".card[data-id='{task}']", f".cell[data-epic='{epic}'][data-status='done']"
        )
        await expect(cell(w, epic, "done").locator(f".card[data-id='{task}']")).to_be_visible()

        # Move it to the other story, then add a link to its sibling and take it away again. The
        # card shows it is done first, as a person would see before going on.
        await w.go(f"#/board?epic={epic}&card={task}")
        await expect(w.page.locator("#panel .card-detail .chips")).to_contain_text("Done")
        await w.page.locator("#move-parent").select_option(str(other_story))
        await w.page.get_by_role("button", name="Move", exact=True).click()
        await expect(
            w.page.locator("#panel .card-detail > p", has_text="In story")
        ).to_contain_text(f"#{other_story} Profile")
        await w.page.locator("#link-relation").select_option("relates_to:out")
        await w.page.locator("#link-target").fill(f"#{friend}")
        await w.page.get_by_role("button", name="Add", exact=True).click()
        removal = w.page.locator("#panel .remove-link")
        await expect(removal).to_have_count(1)
        await removal.click()
        await expect(removal).to_have_count(0)
        assert w.problems == [], w.problems

    # Through MCP, the graph holds exactly that.
    final = await node(client, task)
    assert (final["title"], final["body"], final["priority"], final["status"]) == (
        f"Login form, better {word}", "Email and password, then a code.", "high", "done",
    )  # fmt: skip
    assert (final["parent_id"], final["version"]) == (other_story, 5)
    assert (await node(client, epic))["title"] == f"Portal {word}"
    assert [n["id"] for n in (await ok(client, "list_nodes", parent_id=other_story))["items"]] == [
        task
    ]
    events = await history(client, task)
    assert [(e["action"], e["actor"]) for e in events] == [
        ("create", "Ada Lovelace"), ("update", "Ada Lovelace"), ("update", "Ada Lovelace"),
        ("update", "Ada Lovelace"), ("move", "Ada Lovelace"), ("link_added", "Ada Lovelace"),
        ("link_removed", "Ada Lovelace"),
    ]  # fmt: skip
    edit, started, finished, moved = events[1:5]
    assert set(edit["after"]) == {"title", "body", "priority", "version"}
    assert edit["before"]["title"] == f"Login form {word}" and edit["before"]["version"] == 1
    assert edit["after"]["priority"] == "high" and edit["after"]["version"] == 2
    assert (started["before"], started["after"]) == (
        {"status": "new", "version": 2}, {"status": "in_progress", "version": 3},
    )  # fmt: skip
    assert (finished["before"], finished["after"]) == (
        {"status": "in_progress", "version": 3}, {"status": "done", "version": 4},
    )  # fmt: skip
    assert (moved["before"], moved["after"]) == (
        {"parent_id": story, "version": 4}, {"parent_id": other_story, "version": 5},
    )  # fmt: skip
    assert (await ok(client, "list_links", node_id=task))["items"] == []


async def test_the_coverage_panel_shows_what_the_coverage_report_says(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Covered {word}")
    bare = await made(client, "story", f"Bare story {word}", epic)
    covered = await made(client, "story", f"Covered story {word}", epic)
    await make_rule(client, f"Unplaced rule {word}")
    await make_rule(client, f"Placed rule {word}", story_id=covered)
    report = await ok(client, "coverage_report", limit=1)

    async with browser_page(board, token=the_token()) as w:
        await w.open("#/board")
        await w.page.locator("#show-coverage").click()
        panel = w.page.locator("#panel .coverage")
        await expect(panel).to_contain_text(f"Rules in no story ({report['unplaced_rule_count']})")
        await expect(panel).to_contain_text(
            f"Stories with no rule ({report['story_without_rules_count']})"
        )
        # Every page of both lists, as the report gives them.
        more = panel.get_by_role("button", name="Show more")
        for _ in range(100):
            if not await more.count():
                break
            await more.first.click()
            await w.page.wait_for_timeout(300)
        assert await panel.locator("li[data-story]").count() == report["story_without_rules_count"]
        assert await panel.locator("li[data-rule]").count() == report["unplaced_rule_count"]
        await expect(panel.locator(f"[data-story='{bare}']")).to_have_count(1)
        assert await panel.locator(f"[data-story='{covered}']").count() == 0
        await expect(
            panel.locator(f"li[data-rule]:has-text('Unplaced rule {word}')")
        ).to_have_count(1)
        assert w.problems == [], w.problems


# -- 4. what the server refuses, the board refuses in the same words ---------------------------


async def test_nothing_can_be_dropped_on_ready_and_the_refusal_is_the_one_mcp_gives(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Ready {word}")
    story = await made(client, "story", f"Story {word}", epic)
    first = await made(client, "task", f"First {word}", story)
    second = await made(client, "task", f"Second {word}", story)
    await ok(
        client, "add_link", actor="a", source_id=second, target_id=first, link_type="needs_first"
    )
    before = (await node(client, second), len(await history(client, second)))
    said = await refused(
        client, "update_node", actor="x", node_id=second, expected_version=1, status="ready"
    )

    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}")
        await expect(cell(w, epic, "new").locator(f".card[data-id='{second}']")).to_be_visible()
        await expect(w.page.locator(".column-head.status-ready")).to_contain_text(
            "nothing can be dropped"
        )
        await w.page.drag_and_drop(
            f".card[data-id='{second}']", f".cell[data-epic='{epic}'][data-status='ready']"
        )
        refusal = w.page.locator(".notice.refusal .notice-text").first
        await expect(refusal).to_be_visible()
        plain(await refusal.inner_text(), said)
        assert "worked out" in await refusal.inner_text()
        await expect(cell(w, epic, "new").locator(f".card[data-id='{second}']")).to_be_visible()
    assert (await node(client, second), len(await history(client, second))) == before


async def test_a_link_that_would_make_a_cycle_is_refused_in_the_words_mcp_uses(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Cycle {word}")
    story = await made(client, "story", f"Story {word}", epic)
    first = await made(client, "task", f"First {word}", story)
    second = await made(client, "task", f"Second {word}", story)
    await ok(
        client, "add_link", actor="a", source_id=second, target_id=first, link_type="needs_first"
    )
    before = (await ok(client, "list_links", node_id=first))["items"]
    said = await refused(
        client, "add_link", actor="x", source_id=first, target_id=second, link_type="needs_first"
    )

    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}&card={first}")
        await w.page.locator("#link-relation").select_option("needs_first:out")
        await w.page.locator("#link-target").fill(f"#{second} Second {word}")
        await w.page.get_by_role("button", name="Add", exact=True).click()
        problem = w.page.locator("#panel .inline-form .problem").last
        await expect(problem).not_to_be_empty()
        plain(await problem.inner_text(), said)
        # What was typed is still there.
        assert await w.page.locator("#link-target").input_value() == f"#{second} Second {word}"
    assert (await ok(client, "list_links", node_id=first))["items"] == before


async def test_a_stale_edit_is_refused_keeps_what_was_typed_and_offers_the_current_version(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Stale {word}")
    story = await made(client, "story", f"Original title {word}", epic)
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}&card={story}")
        title = w.page.locator("#edit-title")
        await expect(title).to_have_value(f"Original title {word}")
        await title.fill(f"My title {word}")

        # An agent changes it meanwhile. The board does not overwrite the form being typed in.
        await ok(
            client, "update_node", actor="agent-1", node_id=story, expected_version=1,
            title=f"Agent title {word}",
        )  # fmt: skip
        changed = w.page.locator("#panel .notice.changed")
        await expect(changed).to_be_visible(timeout=SLOW)
        await expect(changed).to_contain_text("agent-1")
        assert await title.input_value() == f"My title {word}"

        said = await refused(
            client, "update_node", actor="x", node_id=story, expected_version=1, title="whatever"
        )
        await w.page.get_by_role("button", name="Save changes").click()
        problem = w.page.locator("#panel .edit .problem")
        await expect(problem.locator(".refusal-text")).to_be_visible()
        plain(await problem.locator(".refusal-text").inner_text(), said)
        assert await title.input_value() == f"My title {word}"
        assert (await node(client, story))["title"] == f"Agent title {word}"

        await problem.locator(".load-current").click()
        await expect(w.page.locator("#edit-title")).to_have_value(f"Agent title {word}")
        assert w.problems == [], w.problems


async def test_a_blank_name_is_refused_in_the_words_mcp_uses_and_changes_nothing(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Nameless {word}")
    story = await made(client, "story", f"Story {word}", epic)
    before = (await node(client, story), len(await history(client, story)))
    said = await refused(
        client, "update_node", actor="  ", node_id=story, expected_version=1, title="whatever"
    )
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}&card={story}")
        await w.page.evaluate("localStorage.setItem('vrum.actor', '   ')")
        await w.page.locator("#edit-title").fill(f"Changed {word}")
        await w.page.get_by_role("button", name="Save changes").click()
        problem = w.page.locator("#panel .edit .problem .refusal-text")
        await expect(problem).to_be_visible()
        plain(await problem.inner_text(), said)
        assert "who is making this change" in await problem.inner_text()
        assert await w.page.locator("#edit-title").input_value() == f"Changed {word}"
    assert (await node(client, story), len(await history(client, story))) == before


# -- 5. the index follows the graph -----------------------------------


async def waiting(client: Client) -> int:
    page = await ok(client, "search", query="anything at all", mode="words", limit=1)
    return int(page["index"]["waiting_to_index"])


async def wait_for_zero(client: Client) -> None:
    deadline = time.monotonic() + 30
    while await waiting(client):
        assert time.monotonic() < deadline, "the index did not catch up"
        await asyncio.sleep(0.5)


async def test_an_edit_on_the_board_is_searchable_at_once_and_indexed_without_a_reindex(
    client: Client, board: str
) -> None:
    word, later = unique_word(), unique_word()
    epic = await made(client, "epic", f"Index {word}")
    story = await made(client, "story", f"Indexed story {word}", epic)
    await reindexed(client)

    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}&card={story}")
        await w.page.locator("#edit-body").fill(f"The phrase {word} appears here")
        await w.page.get_by_role("button", name="Save changes").click()
        await expect(w.page.locator("#panel .card-detail")).to_contain_text(f"The phrase {word}")

        found = await ok(client, "search", query=word, mode="words")
        assert [item["id"] for item in found["items"] if item["item_type"] == "story"] == [story]
        await wait_for_zero(client)
        await expect(w.page.locator("#index-state")).to_have_text("Index: 0 waiting", timeout=SLOW)

        # With the embedding service gone the edit still saves, and one item waits.
        with embeddings_stopped():
            before = await waiting(client)
            await w.page.locator("#edit-body").fill(f"The second phrase {later} appears here")
            await w.page.get_by_role("button", name="Save changes").click()
            await expect(w.page.locator("#panel .card-detail")).to_contain_text(
                f"second phrase {later}"
            )
            assert f"second phrase {later}" in (await node(client, story))["body"]
            assert await waiting(client) == before + 1
            await expect(w.page.locator("#index-state")).to_have_text(
                "Index: 1 waiting", timeout=SLOW
            )
            assert [i["id"] for i in (await ok(client, "search", query=later))["items"]] == [story]

        # When it is back the count falls to zero by itself and the story is found by meaning too.
        await wait_for_zero(client)
        await expect(w.page.locator("#index-state")).to_have_text("Index: 0 waiting", timeout=SLOW)
        again = await ok(
            client, "search", query=f"Indexed story {word} The second phrase {later} appears here"
        )
        assert {i["id"]: i["matched"] for i in again["items"]}[story] == "both"
        assert w.problems == [], w.problems


# -- 6. what is shown is only text, and it all comes from the server's own address -------------


async def test_titles_with_markup_are_shown_as_text_nothing_runs_and_nothing_leaves_the_server(
    client: Client, board: str
) -> None:
    word = unique_word()
    markup = f'<img src=x onerror="window.__ran=1"><script>window.__ran=2</script> {word}'
    epic = await made(client, "epic", markup, body="<b>bold</b> <script>window.__ran=3</script>")
    story = await made(client, "story", f"<i>{word}</i>", epic)

    status, headers, _ = http("GET", board)
    policy = headers["content-security-policy"]
    assert status == 200 and "default-src 'none'" in policy and "script-src 'self'" in policy
    assert "'unsafe-inline'" not in policy and "http" not in policy
    _, api_headers, _ = http("GET", board + "/api/info", token=the_token())
    assert api_headers["content-security-policy"] == policy

    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}")
        await expect(w.page.locator(f"section.lane[data-epic='{epic}']")).to_contain_text(markup)
        await w.page.locator(".lane-title").click()
        await expect(w.page.locator("#panel .body-text")).to_contain_text("<b>bold</b> <script>")
        await card(w, story).click()
        await expect(w.page.locator("#card-title")).to_contain_text(f"<i>{word}</i>")
        await w.go(f"#/graph?epic={epic}")
        await expect(w.page.locator(f".dot[data-id='{epic}']")).to_be_visible()

        assert await w.page.evaluate("typeof window.__ran") == "undefined"
        assert await w.page.locator("img[src='x'], script:not([src]), b").count() == 0
        assert w.dialogs == [] and w.problems == [], (w.dialogs, w.problems)
        assert w.requests and all(url.startswith(w.origin) for url in w.requests), w.requests


# -- the keyboard -----------------------------------


async def test_a_card_can_be_opened_and_moved_from_the_keyboard_alone(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Keys {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Keyed task {word}", story)
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}")
        target = card(w, task)
        await expect(target).to_be_visible()
        await target.focus()
        await w.page.keyboard.press("Enter")
        await expect(w.page.locator("#card-title")).to_contain_text(f"#{task} ")
        await w.page.keyboard.press("Tab")  # leaves the heading; the card is on the board
        await target.locator(".move").focus()
        await w.page.keyboard.press("Enter")
        menu = w.page.locator(".menu[role=menu]")
        await expect(menu).to_be_visible()
        assert await menu.get_by_role("menuitem").all_inner_texts() == [
            "▶ In progress", "■ Blocked", "✓ Done", "✕ Dropped"
        ]  # fmt: skip
        assert "Ready is worked out" in await menu.inner_text()
        await w.page.keyboard.press("Enter")  # the first choice has the focus: in progress
        await expect(
            cell(w, epic, "in_progress").locator(f".card[data-id='{task}']")
        ).to_be_visible()
        assert (await node(client, task))["status"] == "in_progress"
        assert w.problems == [], w.problems


async def test_facts_and_a_story_without_a_rule_show_in_the_card(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Known {word}")
    story = await made(client, "story", f"Story {word}", epic, target_on="2031-05-06")
    fact = await make_fact(client, f"A fact {word}", node_ids=[story])
    await ok(
        client, "record_belief", actor="a", text=f"A belief {word}", evidence_fact_ids=[fact["id"]],
        node_ids=[story],
    )  # fmt: skip
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}&card={story}")
        detail = w.page.locator("#panel .card-detail")
        await expect(detail).to_contain_text("Target 2031-05-06")
        await expect(detail.locator("li.fact")).to_contain_text(f"A fact {word}")
        await expect(detail.locator("li.fact")).to_contain_text("Origin: stated")
        belief = detail.locator("li.belief")
        await expect(belief).to_contain_text("Suggestion")
        await expect(belief).to_contain_text("Origin: stated")
        await expect(belief).to_contain_text("Rests on 1 piece of evidence")
        assert w.problems == [], w.problems
