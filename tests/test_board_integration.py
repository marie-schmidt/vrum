"""M10 against the running containers: the Board, its cards and the full page every card opens,
driven in a real headless browser and checked against a real MCP client (see ./check.sh). What a
person does on the board is checked by reading the graph back through the tools; what an agent does
through the tools is checked by looking at the board. Nothing opens at the side of a page: a card, a
lane, a result and a record all open the item's own page."""

import asyncio
import json
import re
import time
from typing import Any
from urllib.parse import urlparse

import pytest
from mcp import Client
from playwright.async_api import expect

from tests.board_helpers import (
    Watched,
    api_path,
    apply_filter,
    board_call,
    board_url,
    browser_page,
    card,
    cards_are,
    cell,
    create_in_browser,
    crumbs,
    database_footprint,
    http,
    item_rows,
    lane,
    made,
    no_panel_or_drawer,
    on_page,
    open_editor,
    open_item,
    plain,
)
from tests.helpers import (
    all_nodes,
    code_of,
    confirm_on_board,
    connect,
    connect_as,
    connect_each,
    day,
    embeddings_stopped,
    make_note,
    make_rule,
    number_of,
    ok,
    refused,
    reindexed,
    the_token,
    unique_word,
)
from tests.world import SESSION_COOKIE, api, make_project, project_at, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SLOW = 15_000  # what the board may take to show an agent's work: the poll is every 5 seconds


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def node(client: Client, ref: str) -> dict[str, Any]:
    return await ok(client, "get_node", node=ref)


async def create_on_board(w: Watched, kind: str, title: str, parent: str | None = None) -> str:
    """Make an item with the Board's own form. The form's button is on the Board, and it goes to the
    page of the new item, so each one starts from the Board again."""
    await w.go("#/board")
    await on_page(w.page, "board")
    return await create_in_browser(w.page, kind, title, parent)


async def history(client: Client, ref: str) -> list[dict[str, Any]]:
    return list((await ok(client, "node_history", node=ref, limit=200))["items"])


# -- 1. signing in ---------------------------------------------------------------------------------


async def test_every_data_call_needs_a_signed_in_owner_and_the_pages_themselves_hold_no_data(
    client: Client, board: str
) -> None:
    project = project_at(board)
    owner = project.owner.sign_in_again()
    wrong = "vrs_wrong-session-" + unique_word()
    origin = board.split("/board")[0]
    mine = api_path(board)
    word = unique_word()
    epic = await made(client, "epic", f"Guarded {word}")

    # The page files hold no data, so they are open, and hold no session, secret or key.
    status, headers, text = http("GET", board)
    assert status == 200 and "Vrum board" in text
    for secret in (owner.cookie, owner.csrf, project.key):
        assert secret not in text and secret not in str(headers)

    # Every data call needs a session, says so in plain words, and never repeats what was sent.
    calls: list[tuple[str, str, Any]] = [
        ("GET", "/board/api/info", None),
        ("GET", "/board/api/tools", None),
        ("GET", "/board/api/projects", None),
        ("GET", mine, None),
        ("GET", f"{mine}/index", None),
        ("GET", f"{mine}/keys", None),
        ("POST", f"{mine}/tool/list_nodes", {}),
        ("POST", f"{mine}/tool/update_node", {"node": epic}),
        ("POST", f"{mine}/try/list_nodes", {}),
    ]
    for method, path, body in calls:
        for cookie in (None, wrong):
            reply = api(origin, method, path, body, cookie=cookie)
            assert reply.status == 401, (method, path, reply.body)
            assert "not signed in" in reply.body["message"], (path, reply.body)
            assert wrong not in json.dumps(reply.body), path

    # Signed in, a read needs nothing more.
    reply = owner.call("GET", "/board/api/info")
    assert reply.status == 200 and "poll_seconds" in reply.body
    # A change also needs the session's own secret and the server's own address as its origin, so
    # that a page of another website cannot make this browser change anything.
    change = {"node": epic, "expected_version": 1, "title": f"Changed {word}"}
    path = f"{mine}/tool/update_node"
    for csrf, origin_sent, extra in (
        (None, "same", None),
        ("not-the-secret-" + unique_word(), "same", None),
        (owner.csrf, "https://elsewhere.example", None),
        (owner.csrf, "same", {"Sec-Fetch-Site": "cross-site"}),
    ):
        reply = api(
            origin, "POST", path, change, cookie=owner.cookie, csrf=csrf, origin=origin_sent,
            extra=extra,
        )  # fmt: skip
        assert reply.status == 403, (csrf, origin_sent, extra, reply.body)
        assert "nothing was done" in reply.body["message"]
        assert (await ok(client, "get_node", node=epic))["version"] == 1
    assert owner.call("POST", path, change).status == 200
    assert (await ok(client, "get_node", node=epic))["version"] == 2

    # Another person's project is as good as no project: the same words, so that they say nothing
    # about which projects exist.
    stranger = register(origin)
    theirs = stranger.call("GET", mine)
    nowhere = stranger.call("GET", "/board/api/projects/999999999")
    assert theirs.status == nowhere.status == 404 and theirs.body == nowhere.body
    for rest, body in (("/tool/list_nodes", {}), ("/index", None), ("/keys", None)):
        refusal = stranger.call("POST" if rest.startswith("/tool") else "GET", mine + rest, body)
        assert refusal.status == 404 and refusal.body == theirs.body, rest
    assert project.name not in json.dumps(theirs.body)


async def test_a_person_signs_in_with_email_and_password_and_the_session_stays_out_of_the_page(
    board: str,
) -> None:
    project = project_at(board)
    owner = project.owner
    wrong = "not-the-password-" + unique_word()

    async def submit(w: Watched, button: str) -> int:
        """Press a button of the sign-in page and give the status the server answered with."""
        async with w.page.expect_response(
            lambda r: r.request.method == "POST" and "/board/api/" in r.url
        ) as sent:
            await w.page.locator(button).click()
        return (await sent.value).status

    async with browser_page(board, token=None, name=None) as w:
        await w.open()
        await expect(w.page.locator("h1")).to_have_text("Sign in to Vrum")
        await expect(w.page.locator("#sign-in-form")).to_be_visible()
        assert (
            await w.page.locator("#main-nav").count() == 0
            and not await w.page.locator(".board").count()
        )
        problem = w.page.locator("#entry-problem")

        # Nothing typed, then a wrong password: the same plain words, and what was typed is never
        # shown back.
        assert await submit(w, "#sign-in-submit") == 401
        await expect(problem).to_contain_text("email address or the password is not right")
        await w.page.locator("#account-email").fill(owner.email)
        await w.page.locator("#account-password").fill(wrong)
        assert await submit(w, "#sign-in-submit") == 401
        await expect(problem).to_contain_text("email address or the password is not right")
        assert wrong not in await w.page.locator("body").inner_text()
        assert await w.page.locator("#main-nav").count() == 0

        # The right password opens the project in the address, on the Board, as the account's name.
        await w.page.locator("#account-password").fill(owner.password)
        assert await submit(w, "#sign-in-submit") == 200
        await expect(w.page.locator("h1")).to_have_text("Board", timeout=SLOW)
        await expect(w.page.locator("#account")).to_contain_text(owner.name)
        await expect(w.page.locator("#project-switcher")).to_have_value(str(project.id))
        # Who makes a change is the account, which the server knows from the session: the page
        # keeps no name of its own to type or to store.
        assert await w.page.evaluate("localStorage.getItem('vrum.actor')") is None

        # The session is a cookie that a script on the page cannot read, that is not sent to
        # another site, and that goes only to the board's own addresses.
        cookies = [c for c in await w.page.context.cookies() if c["name"] == SESSION_COOKIE]
        assert len(cookies) == 1
        cookie = cookies[0]
        assert cookie["httpOnly"] and cookie["sameSite"] == "Strict" and cookie["path"] == "/board"
        assert await w.page.evaluate("document.cookie") == ""
        # It, and the agent key, are in no address, no storage and nothing the page shows.
        everything = " ".join(
            [
                w.page.url,
                await w.page.evaluate("JSON.stringify(localStorage)"),
                await w.page.evaluate("JSON.stringify(sessionStorage)"),
                await w.page.evaluate("document.documentElement.outerHTML"),
                *w.requests,
            ]
        )
        for secret in (cookie["value"], project.key):
            assert secret not in everything
        # The two failed attempts were refused with a 401, and the browser logs each as a failed
        # load; nothing else went wrong.
        assert all("status of 401" in problem_ for problem_ in w.problems), w.problems


async def test_a_person_registers_makes_a_project_switches_signs_out_and_back_in(
    board: str,
) -> None:
    origin = board.split("/board")[0]
    word = unique_word()
    email, secret, shown = f"{word}@example.test", f"correct-horse-{word}", f"Grace {word}"

    async def press(w: Watched, button: str) -> int:
        async with w.page.expect_response(
            lambda r: r.request.method == "POST" and "/board/api/" in r.url
        ) as sent:
            await w.page.locator(button).click()
        return (await sent.value).status

    async with browser_page(f"{origin}/board", token=None, name=None) as w:
        await w.open()
        await w.page.locator("#show-register").click()
        await expect(w.page.locator("h1")).to_have_text("Create an account")
        await w.page.locator("#account-email").fill(email)
        await w.page.locator("#account-name").fill(shown)
        problem = w.page.locator("#entry-problem")

        # The server's own words for what is wrong, and nothing is made.
        await w.page.locator("#account-password").fill("short")
        assert await press(w, "#register-submit") == 400
        await expect(problem).to_contain_text("The password must be 10 to 200 characters")
        await w.page.locator("#account-email").fill("not an address")
        await w.page.locator("#account-password").fill(secret)
        assert await press(w, "#register-submit") == 400
        await expect(problem).to_contain_text("does not look like an email address")
        existing = project_at(board).owner.email
        await w.page.locator("#account-email").fill(existing)
        assert await press(w, "#register-submit") == 409
        await expect(problem).to_contain_text("already exists")
        assert secret not in await w.page.locator("body").inner_text()

        # A good registration signs the person in and shows an honest empty list of projects.
        await w.page.locator("#account-email").fill(email)
        assert await press(w, "#register-submit") == 201
        await expect(w.page.locator("#no-projects")).to_contain_text(shown, timeout=SLOW)
        assert await w.page.locator("#projects").count() == 0
        await expect(w.page.locator("#account")).to_have_text(shown)
        await expect(w.page.locator("#sign-out")).to_be_visible()

        # Making a project opens its board, on the Board, with changes recorded under the person's
        # name.
        assert await press(w, "#create-project") == 400
        await expect(w.page.locator("#project-problem")).to_contain_text("Give the project a name")
        await w.page.locator("#project-name").fill(f"First {word}")
        assert await press(w, "#create-project") == 201
        await expect(w.page.locator("h1")).to_have_text("Board", timeout=SLOW)
        assert re.fullmatch(r"/board/p/\d+", urlparse(w.page.url).path)
        first = int(urlparse(w.page.url).path.rsplit("/", 1)[1])
        await expect(w.page.locator("#account")).to_contain_text(shown)
        await expect(w.page.locator("#project-switcher")).to_have_value(str(first))
        assert await w.page.locator("#project-switcher option").all_inner_texts() == [
            f"First {word}", "All projects…"
        ]  # fmt: skip

        # The project list of the same account, a second project, and the switcher's own choice.
        await w.page.locator("#project-switcher").select_option("")
        await expect(w.page.locator("#projects .project")).to_have_count(1, timeout=SLOW)
        await w.page.locator("#project-name").fill(f"First {word}")
        assert await press(w, "#create-project") == 409
        await expect(w.page.locator("#project-problem")).to_contain_text("already have a project")
        await w.page.locator("#project-name").fill(f"Second {word}")
        assert await press(w, "#create-project") == 201
        await expect(w.page.locator("h1")).to_have_text("Board", timeout=SLOW)
        second = int(urlparse(w.page.url).path.rsplit("/", 1)[1])
        assert second != first
        await w.page.locator("#project-switcher").select_option(str(first))
        await expect(w.page.locator("#project-switcher")).to_have_value(str(first), timeout=SLOW)
        assert urlparse(w.page.url).path == f"/board/p/{first}"
        await expect(w.page.locator("h1")).to_have_text("Board")

        # Signing out ends the session at the server, not only in the page.
        old = (await w.page.context.cookies())[0]["value"]
        await w.page.locator("#sign-out").click()
        await expect(w.page.locator("#sign-in-form")).to_be_visible(timeout=SLOW)
        assert urlparse(w.page.url).path == "/board"
        assert [c for c in await w.page.context.cookies() if c["value"] == old] == []
        stale = api(origin, "GET", "/board/api/projects", cookie=old)
        assert stale.status == 401 and "not signed in" in stale.body["message"]

        # Signing in again lists both projects, and opens one.
        await w.page.locator("#account-email").fill(email.upper())
        await w.page.locator("#account-password").fill(secret)
        assert await press(w, "#sign-in-submit") == 200
        await expect(w.page.locator("#projects .project")).to_have_count(2, timeout=SLOW)
        assert await w.page.locator("#projects .project a").all_inner_texts() == [
            f"First {word}", f"Second {word}"
        ]  # fmt: skip
        await w.page.locator("#projects .project a", has_text=f"Second {word}").click()
        await expect(w.page.locator("h1")).to_have_text("Board", timeout=SLOW)
        assert urlparse(w.page.url).path == f"/board/p/{second}"

        # Another person's project is not opened by its address: the page says so and shows
        # nothing of it.
        elsewhere = project_at(board)
        await w.page.goto(f"{origin}/board/p/{elsewhere.id}")
        await expect(w.page.locator("#no-such-project")).to_contain_text("no project with that")
        assert await w.page.locator("#main-nav").count() == 0
        assert elsewhere.name not in await w.page.locator("body").inner_text()
        # The browser logs a refusal it was sent (a 409, a 404) as a failed load; nothing else
        # went wrong.
        assert all(
            "status of 404" in problem_
            or "status of 409" in problem_
            or "status of 401" in problem_
            for problem_ in w.problems
        ), w.problems


# -- 2. an agent's work shows up -----------------------------------


async def test_what_agents_do_through_mcp_shows_on_the_board_within_ten_seconds_with_no_reload(
    client: Client, board: str
) -> None:
    word = unique_word()
    one, two = f"agent-1-{word}", f"agent-2-{word}"
    async with (
        connect_each(one, two) as (by_one, by_two),
        browser_page(board, token=the_token()) as w,
    ):
        await w.open("#/board?show=both")
        await expect(w.page.locator(".board")).to_be_visible()
        marker = await w.page.evaluate("window.__same = 'same page'; window.__same")

        epic = await made(by_one, "epic", f"Billing {word}")
        story = await made(by_one, "story", f"Invoices {word}", epic)
        first = await made(by_one, "task", f"Template {word}", story)
        second = await made(by_one, "task", f"Send it {word}", story)
        await ok(by_one, "add_link", source=second, target=first, link_type="needs_first")
        await ok(by_two, "claim_node", node=first)
        await ok(by_two, "update_node", node=first, expected_version=1, status="in_progress")

        started = time.monotonic()
        lane = w.page.locator(f"section.lane[data-ref='{epic}']")
        await expect(lane).to_be_visible(timeout=SLOW)
        await expect(
            cell(w.page, epic, "ready").locator(f".card[data-ref='{story}']")
        ).to_be_visible()
        working = cell(w.page, epic, "in_progress").locator(f".card[data-ref='{first}']")
        await expect(working).to_contain_text(f"Held by {two}")
        waiting = cell(w.page, epic, "new").locator(f".card[data-ref='{second}']")
        # A card that waits says one word, Waiting, and the hover card says what it waits for.
        await expect(waiting.locator(".card-waiting")).to_have_text("Waiting")
        await waiting.locator(".card-waiting").hover()
        await expect(w.page.locator(".hovercard:visible")).to_contain_text(
            f"{code_of(first)} Template {word}"
        )
        await w.page.mouse.move(0, 0)
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
    await ok(client, "update_node", node=done, expected_version=1, status="done")
    await ok(client, "update_node", node=dropped, expected_version=1, status="dropped")

    by_ref = {item["ref"]: item for item in (await ok(client, "list_nodes", parent=epic))["items"]}
    assert (await node(client, story))["progress_done"] == by_ref[story]["progress_done"] == 1
    assert (await node(client, story))["progress_total"] == by_ref[story]["progress_total"] == 2
    assert (await node(client, epic))["progress_done"] == 1
    assert (await node(client, epic))["progress_total"] == 2
    assert (await node(client, other))["progress_total"] == 0
    assert (await node(client, open_))["progress_done"] is None

    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={number_of(epic)}&show=both")
        await expect(w.page.locator(f"section.lane[data-ref='{epic}']")).to_contain_text(
            "1 of 2 tasks done"
        )
        await expect(card(w.page, story)).to_contain_text("1 of 2 tasks done")
        await expect(card(w.page, other)).to_contain_text("No tasks yet")
        assert w.problems == [], w.problems


# -- 3. what a person does on the board is what the graph then holds -------------------------------


async def test_what_a_person_does_on_the_board_is_in_the_graph_under_their_name() -> None:
    word = unique_word()
    # The person is the account they are signed in with: every change is recorded under its name.
    project = make_project(register(name="Ada Lovelace"), f"Ada's {word}")
    async with (
        connect(project.mcp_url, project.key) as client,
        browser_page(project.address, token=project.key) as w,
    ):
        await w.open("#/board")
        await expect(w.page.locator("#account")).to_contain_text("Ada Lovelace")

        epic = await create_on_board(w, "epic", f"Portal {word}")
        story = await create_on_board(w, "story", f"Sign in {word}", epic)
        other_story = await create_on_board(w, "story", f"Profile {word}", epic)
        task = await create_on_board(w, "task", f"Login form {word}", story)
        friend = await create_on_board(w, "task", f"Logout {word}", story)
        # Each item the form made opened on its own page, which is in the Board.
        assert "from=board" in w.page.url and f"item={friend}" in w.page.url

        # The title of a card is the link to its page, and the page is "in" the Board.
        scope = f"#/board?epic={number_of(epic)}"
        await w.go(scope)
        await card(w.page, task).locator("h3.card-title a").click()
        await expect(w.page.locator("#item-page")).to_have_attribute("data-ref", task, timeout=SLOW)
        assert f"item={task}" in w.page.url and "from=board" in w.page.url
        await expect(w.page.locator("#item-title")).to_contain_text(f"{code_of(task)} ")
        await open_editor(w.page)
        await w.page.locator("#edit-title").fill(f"Login form, better {word}")
        await w.page.locator("#edit-body").fill("Email and password, then a code.")
        await w.page.locator("#edit-priority").select_option("high")
        await w.page.get_by_role("button", name="Save changes").click()
        await expect(w.page.locator("#item-details .body-text")).to_contain_text(
            "Email and password"
        )

        # The way back is the Board as it was left, still scoped to the epic.
        await w.page.locator("nav.crumbs .crumb-view a").click()
        await expect(card(w.page, task)).to_be_visible(timeout=SLOW)
        assert w.page.url.endswith(scope)

        # Drag it to in progress, then to done: the same two changes an agent would make.
        await w.page.drag_and_drop(
            f".card[data-ref='{task}']", f".cell[data-epic='{epic}'][data-status='in_progress']"
        )
        await expect(
            cell(w.page, epic, "in_progress").locator(f".card[data-ref='{task}']")
        ).to_be_visible()
        await w.page.drag_and_drop(
            f".card[data-ref='{task}']", f".cell[data-epic='{epic}'][data-status='done']"
        )
        await expect(
            cell(w.page, epic, "done").locator(f".card[data-ref='{task}']")
        ).to_be_visible()

        # Open it again, this time by pressing the card (not its title): the page shows it is done,
        # and says where it is, as a person would see before going on.
        await card(w.page, task).locator(".card-context").click()
        await expect(w.page.locator("#item-page")).to_have_attribute("data-ref", task, timeout=SLOW)
        await expect(w.page.locator(".card-summary .chips")).to_contain_text("Done")
        # The path names the epic, the story and the task by their codes alone; the titles are in
        # each step's tooltip and in the page's heading.
        assert await crumbs(w.page) == [
            "Projects", project.name, "Board", code_of(epic), code_of(story), code_of(task),
        ]  # fmt: skip
        assert await w.page.locator("#crumb-page").get_attribute("title") == (
            f"{code_of(task)} Login form, better {word}"
        )

        # Move it to the other story, then add a link to its sibling and take it away again.
        await w.page.locator("#move-parent").select_option(other_story)
        await w.page.get_by_role("button", name="Move", exact=True).click()
        await expect(w.page.locator("nav.crumbs li.crumb-item a").last).to_have_attribute(
            "title", re.compile(rf"^{re.escape(code_of(other_story))} Profile\b"), timeout=SLOW
        )
        assert (await crumbs(w.page))[-2] == code_of(other_story)
        await w.page.locator("#link-relation").select_option("relates_to:out")
        await w.page.locator("#link-target").fill(code_of(friend))
        await w.page.get_by_role("button", name="Add", exact=True).click()
        removal = w.page.locator("#item-page .remove-link")
        await expect(removal).to_have_count(1)
        await removal.click()
        await expect(removal).to_have_count(0)
        assert w.problems == [], w.problems

        # Through MCP, the graph holds exactly that.
        final = await node(client, task)
        assert (final["title"], final["body"], final["priority"], final["status"]) == (
            f"Login form, better {word}", "Email and password, then a code.", "high", "done",
        )  # fmt: skip
        assert (final["parent"], final["version"]) == (other_story, 5)
        assert (await node(client, epic))["title"] == f"Portal {word}"
        listed = (await ok(client, "list_nodes", parent=other_story))["items"]
        assert [n["ref"] for n in listed] == [task]
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
            {"parent": story, "version": 4}, {"parent": other_story, "version": 5},
        )  # fmt: skip
        assert (await ok(client, "list_links", node=task))["items"] == []


async def test_the_boards_coverage_link_leads_to_the_coverage_page_that_shows_what_the_report_says(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Covered {word}")
    bare = await made(client, "story", f"Bare story {word}", epic)
    covered = await made(client, "story", f"Covered story {word}", epic)
    await make_rule(client, f"Unplaced rule {word}")
    await make_rule(client, f"Placed rule {word}", node=covered)
    report = await ok(client, "coverage_report", limit=1)

    async with browser_page(board, token=the_token()) as w:
        await w.open("#/board")
        link = w.page.locator("#show-coverage")
        await expect(link).to_have_attribute("href", "#/coverage")
        await link.click()
        await on_page(w.page, "coverage")
        assert w.page.url.endswith("#/coverage")
        coverage = w.page.locator("#coverage-page")
        await expect(w.page.locator("#coverage-summary")).to_have_attribute(
            "data-unplaced", str(report["unplaced_rule_count"]), timeout=SLOW
        )
        await expect(w.page.locator("#coverage-summary")).to_have_attribute(
            "data-stories-without", str(report["story_without_rules_count"])
        )
        await expect(coverage).to_contain_text(
            f"Acceptance criteria placed nowhere ({report['unplaced_rule_count']})"
        )
        await expect(coverage).to_contain_text(
            f"Stories with no acceptance criterion ({report['story_without_rules_count']})"
        )
        # Every page of both lists, as the report gives them.
        more = coverage.get_by_role("button", name="Show more")
        for _ in range(100):
            if not await more.count():
                break
            await more.first.click()
            await w.page.wait_for_timeout(300)
        stories = coverage.locator("#stories-without-rules tr[data-story]")
        rules = coverage.locator("#unplaced-rules tr[data-rule]")
        assert await stories.count() == report["story_without_rules_count"]
        assert await rules.count() == report["unplaced_rule_count"]
        await expect(coverage.locator(f"tr[data-story='{bare}']")).to_have_count(1)
        assert await coverage.locator(f"tr[data-story='{covered}']").count() == 0
        await expect(rules.filter(has_text=f"Unplaced rule {word}")).to_have_count(1)
        # An acceptance criterion is a rule in the database and the tools, never on the board.
        assert "Rules placed nowhere" not in await coverage.inner_text()
        assert "equirement" not in await coverage.inner_text()

        # A row opens the page of its story, which is still "in" the coverage report, and the way
        # back is that report.
        await coverage.locator(f"tr[data-story='{bare}'] a.record-title").click()
        await expect(w.page.locator("#item-page")).to_have_attribute("data-ref", bare, timeout=SLOW)
        assert "from=coverage" in w.page.url
        assert (await crumbs(w.page))[2] == "Coverage"
        await w.page.locator("nav.crumbs .crumb-view a").click()
        await on_page(w.page, "coverage")
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
    await ok(client, "add_link", source=second, target=first, link_type="needs_first")
    before = (await node(client, second), len(await history(client, second)))
    said = await refused(client, "update_node", node=second, expected_version=1, status="ready")

    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={number_of(epic)}")
        await expect(
            cell(w.page, epic, "new").locator(f".card[data-ref='{second}']")
        ).to_be_visible()
        await expect(w.page.locator(".column-head.status-ready")).to_contain_text("Ready")
        await w.page.drag_and_drop(
            f".card[data-ref='{second}']", f".cell[data-epic='{epic}'][data-status='ready']"
        )
        refusal = w.page.locator(".notice.refusal .notice-text").first
        await expect(refusal).to_be_visible()
        plain(await refusal.inner_text(), said)
        assert "worked out" in await refusal.inner_text()
        await expect(
            cell(w.page, epic, "new").locator(f".card[data-ref='{second}']")
        ).to_be_visible()
    assert (await node(client, second), len(await history(client, second))) == before


async def test_a_link_that_would_make_a_cycle_is_refused_in_the_words_mcp_uses(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Cycle {word}")
    story = await made(client, "story", f"Story {word}", epic)
    first = await made(client, "task", f"First {word}", story)
    second = await made(client, "task", f"Second {word}", story)
    await ok(client, "add_link", source=second, target=first, link_type="needs_first")
    before = (await ok(client, "list_links", node=first))["items"]
    said = await refused(client, "add_link", source=first, target=second, link_type="needs_first")

    async with browser_page(board, token=the_token()) as w:
        await open_item(w, first)
        await w.page.locator("#link-relation").select_option("needs_first:out")
        await w.page.locator("#link-target").fill(f"{code_of(second)} Second {word}")
        await w.page.get_by_role("button", name="Add", exact=True).click()
        problem = w.page.locator("#item-page form.add-link .problem")
        await expect(problem).not_to_be_empty()
        plain(await problem.inner_text(), said)
        # What was typed is still there.
        assert (
            await w.page.locator("#link-target").input_value() == f"{code_of(second)} Second {word}"
        )
    assert (await ok(client, "list_links", node=first))["items"] == before


async def test_a_stale_edit_is_refused_keeps_what_was_typed_and_offers_the_current_version(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Stale {word}")
    story = await made(client, "story", f"Original title {word}", epic)
    async with browser_page(board, token=the_token()) as w:
        await open_item(w, story)
        await open_editor(w.page)
        title = w.page.locator("#edit-title")
        await expect(title).to_have_value(f"Original title {word}")
        await title.fill(f"My title {word}")

        # An agent changes it meanwhile. The board does not overwrite the form being typed in.
        async with connect_as(f"agent-1-{word}") as by_agent:
            await ok(
                by_agent, "update_node", node=story, expected_version=1,
                title=f"Agent title {word}",
            )  # fmt: skip
        changed = w.page.locator("#item-page .notice.changed")
        await expect(changed).to_be_visible(timeout=SLOW)
        await expect(changed).to_contain_text(f"agent-1-{word}")
        assert await title.input_value() == f"My title {word}"

        said = await refused(
            client, "update_node", node=story, expected_version=1, title="whatever"
        )
        await w.page.get_by_role("button", name="Save changes").click()
        problem = w.page.locator("#item-page form.edit .problem")
        await expect(problem.locator(".refusal-text")).to_be_visible()
        plain(await problem.locator(".refusal-text").inner_text(), said)
        assert await title.input_value() == f"My title {word}"
        assert (await node(client, story))["title"] == f"Agent title {word}"

        await problem.locator(".load-current").click()
        await expect(w.page.locator("#edit-title")).to_have_value(f"Agent title {word}")
        assert w.problems == [], w.problems


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
        await open_item(w, story)
        await open_editor(w.page)
        await w.page.locator("#edit-body").fill(f"The phrase {word} appears here")
        await w.page.get_by_role("button", name="Save changes").click()
        await expect(w.page.locator("#item-details .body-text")).to_contain_text(
            f"The phrase {word}"
        )

        found = await ok(client, "search", query=word, mode="words")
        assert [item["ref"] for item in found["items"] if item["item_type"] == "story"] == [story]
        await wait_for_zero(client)
        # With nothing waiting the top bar says nothing about the index.
        await expect(w.page.locator("#index-state")).to_be_hidden(timeout=SLOW)

        # With the embedding service gone the edit still saves, and one item waits.
        with embeddings_stopped():
            before = await waiting(client)
            await open_editor(w.page)
            await w.page.locator("#edit-body").fill(f"The second phrase {later} appears here")
            await w.page.get_by_role("button", name="Save changes").click()
            await expect(w.page.locator("#item-details .body-text")).to_contain_text(
                f"second phrase {later}"
            )
            assert f"second phrase {later}" in (await node(client, story))["body"]
            assert await waiting(client) == before + 1
            await expect(w.page.locator("#index-state")).to_have_text(
                "Search by meaning is not answering: 1 waiting", timeout=SLOW
            )
            assert [i["ref"] for i in (await ok(client, "search", query=later))["items"]] == [story]

        # When it is back the count falls to zero by itself and the story is found by meaning too.
        await wait_for_zero(client)
        await expect(w.page.locator("#index-state")).to_be_hidden(timeout=SLOW)
        again = await ok(
            client, "search", query=f"Indexed story {word} The second phrase {later} appears here"
        )
        assert {i["ref"]: i["matched"] for i in again["items"]}[story] == "both"
        assert w.problems == [], w.problems


# -- 6. what is shown is only text, and it all comes from the server's own address -------------


async def test_titles_with_markup_are_shown_as_text_nothing_runs_and_nothing_leaves_the_server(
    client: Client, board: str
) -> None:
    word = unique_word()
    markup = f'<img src=x onerror="window.__ran=1"><script>window.__ran=2</script> {word}'
    epic = await made(client, "epic", markup, body="<b>bold</b> <script>window.__ran=3</script>")
    story = await made(client, "story", f"<i>{word}</i>", epic)
    note = await make_note(
        client,
        f'<img src=x onerror="window.__ran=4"> a note {word}',
        f"<b>the source</b> {word}",
        nodes=[story],
    )
    decision = await make_note(
        client,
        f"<u>A decision</u> {word}\n\n<script>window.__ran=5</script> what was decided",
        '<img src=x onerror="window.__ran=6"> why',
        decided_by="Ada",
        nodes=[story],
    )

    status, headers, _ = http("GET", board)
    policy = headers["content-security-policy"]
    assert status == 200 and "default-src 'none'" in policy and "script-src 'self'" in policy
    assert "'unsafe-inline'" not in policy and "http" not in policy
    answered = board_call(board, "GET", "/board/api/info")
    assert answered.status == 200 and answered.headers["content-security-policy"] == policy

    async with browser_page(board, token=the_token()) as w:
        scope = number_of(epic)
        await w.open(f"#/board?epic={scope}&show=both")
        await expect(w.page.locator(f"section.lane[data-ref='{epic}']")).to_contain_text(markup)
        await w.page.locator("a.lane-title").click()
        await expect(w.page.locator("#item-details .body-text")).to_contain_text(
            "<b>bold</b> <script>"
        )
        await w.page.locator("nav.crumbs .crumb-view a").click()
        await card(w.page, story).locator("h3.card-title a").click()
        await expect(w.page.locator("#item-title")).to_contain_text(f"<i>{word}</i>")
        assert f"<i>{word}</i>" in (
            await w.page.locator("#crumb-page").get_attribute("title") or ""
        )
        # What concerns the story is shown as text too: in the tables of its page, on the page of
        # each record, on the search page, on the activity page and in the Graph.
        context = w.page.locator("#item-page [data-part=context]")
        # A table cell shows the statement as plain words (its Markdown marks are dropped), and
        # the cell's title holds it whole, as written.
        note_row = context.locator(
            f"tr.record-line[data-ref='note:{note['id']}'][data-project-wide=false]"
        )
        await expect(note_row).to_contain_text(
            f'<img src=x onerror="window.__ran=4"> a note {word}'
        )
        await expect(note_row.locator("td.cell-title .clamp")).to_have_attribute(
            "title", re.compile(rf'<img src=x onerror="window\.__ran=4"> a note {word}')
        )
        # Other tests may have made project-wide notes, which every recall shows too.
        decision_row = context.locator(
            f"tr.record-line[data-ref='note:{decision['id']}'][data-project-wide=false]"
        )
        await expect(decision_row).to_contain_text(
            "<script>window.__ran=5</script> what was decided"
        )
        await expect(decision_row.locator("td.cell-title .clamp")).to_have_attribute(
            "title", re.compile(r"<script>window\.__ran=5</script> what was decided")
        )
        await note_row.locator("a.record-title").click()
        await expect(w.page.locator("#item-page")).to_have_attribute(
            "data-ref", f"note:{note['id']}", timeout=SLOW
        )
        await expect(w.page.locator("#item-page [data-part=why]")).to_contain_text(
            f"<b>the source</b> {word}"
        )
        await w.page.go_back()
        await decision_row.locator("a.record-title").click()
        await expect(w.page.locator("#item-page")).to_have_attribute(
            "data-ref", f"note:{decision['id']}", timeout=SLOW
        )
        await expect(w.page.locator("#item-page [data-part=why]")).to_contain_text(
            '<img src=x onerror="window.__ran=6"> why'
        )
        await w.go(f"#/search?search={word}")
        await expect(w.page.locator("#search-results tr.result")).to_have_count(4, timeout=SLOW)
        await expect(w.page.locator("#search-results")).to_contain_text("<img src=x")
        await w.go("#/activity")
        await expect(w.page.locator("#feed")).to_contain_text(f"a note {word}", timeout=SLOW)
        await w.go(f"#/graph?tab=around&focus={story}")
        await expect(w.page.locator("#graph-hub")).to_contain_text(f"<i>{word}</i>", timeout=SLOW)
        await expect(
            w.page.locator(f".graph-around button.graph-node[data-ref='note:{note['id']}']")
        ).to_contain_text("<img src=x", timeout=SLOW)

        assert await w.page.evaluate("typeof window.__ran") == "undefined"
        assert await w.page.locator("img[src='x'], script:not([src]), b, u").count() == 0
        assert w.dialogs == [] and w.problems == [], (w.dialogs, w.problems)
        assert w.requests and all(url.startswith(w.origin) for url in w.requests), w.requests


# -- the keyboard -----------------------------------


async def test_a_card_can_be_opened_and_folded_from_the_keyboard_alone(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Keys {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Keyed task {word}", story)
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={number_of(epic)}")
        target = card(w.page, task)
        await expect(target).to_be_visible()
        # The card is not a stop of its own: its fold button and then its title (a link) are, and a
        # card has no menu to move it: a status is changed by dragging the card to another column.
        assert await target.get_attribute("tabindex") is None
        assert await target.locator("button.move").count() == 0
        link = target.locator("h3.card-title a")
        await target.locator(".fold").focus()
        await w.page.keyboard.press("Enter")
        await expect(target).to_have_class(re.compile(r"\bfolded\b"))
        await w.page.keyboard.press("Space")
        await expect(target).not_to_have_class(re.compile(r"\bfolded\b"))
        await w.page.keyboard.press("Tab")
        await expect(link).to_be_focused()
        await expect(link).to_have_attribute("href", f"#/item?item={task}&from=board")
        await w.page.keyboard.press("Enter")
        await expect(w.page.locator("#item-page")).to_have_attribute("data-ref", task, timeout=SLOW)
        assert w.page.url.endswith(f"#/item?item={task}&from=board")
        await expect(w.page.locator("#item-title")).to_contain_text(f"{code_of(task)} ")
        await expect(w.page.locator("#item-title")).to_be_focused()
        await no_panel_or_drawer(w.page)

        # The way back is a link in the path, reached and used with the keyboard too, and the
        # keyboard carries on from the page.
        back = w.page.locator("#back-to-view")
        await back.focus()
        await w.page.keyboard.press("Enter")
        await expect(target).to_be_visible(timeout=SLOW)
        assert w.page.url.endswith(f"#/board?epic={number_of(epic)}")
        await expect(w.page.locator("main#page")).to_be_focused()
        assert w.problems == [], w.problems


async def test_a_card_folds_away_to_its_title_and_stays_folded_when_the_board_redraws(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Folds {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Folding task {word}", story)
    other = await made(client, "task", f"Other task {word}", story)
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={number_of(epic)}")
        target = card(w.page, task)
        fold = target.locator("button.fold")
        await expect(target).to_be_visible()
        # Open, it says what a click does, and the chips and the story it is under show.
        await expect(fold).to_have_attribute("aria-expanded", "true")
        await expect(fold).to_have_attribute("aria-label", f"Fold away {code_of(task)}")
        await expect(target.locator(".card-context")).to_be_visible()

        # A click folds this card and no other, and does not open its page.
        await fold.click()
        await expect(target).to_have_class(re.compile(r"\bfolded\b"))
        await expect(fold).to_have_attribute("aria-expanded", "false")
        await expect(fold).to_have_attribute("aria-label", f"Show {code_of(task)}")
        await expect(target.locator(".card-context")).to_be_hidden()
        await expect(target.locator("h3.card-title a")).to_be_visible()
        await expect(card(w.page, other)).not_to_have_class(re.compile(r"\bfolded\b"))
        assert w.page.url.endswith(f"#/board?epic={number_of(epic)}")

        # What an agent does redraws the board, and the card stays folded.
        version = (await node(client, task))["version"]
        await ok(
            client, "update_node", node=task, expected_version=version, title=f"Renamed {word}"
        )
        await expect(target.locator("h3.card-title a")).to_contain_text(
            f"Renamed {word}", timeout=SLOW
        )
        await expect(target).to_have_class(re.compile(r"\bfolded\b"))

        # A second click on the fold shows the card again.
        await fold.click()
        await expect(target).not_to_have_class(re.compile(r"\bfolded\b"))
        await expect(target.locator(".card-context")).to_be_visible()
        assert w.problems == [], w.problems


async def test_a_lane_and_the_cards_of_a_story_and_a_task_open_their_full_pages_in_the_board(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Paged {word}")
    story = await made(client, "story", f"Paged story {word}", epic)
    first = await made(client, "task", f"First paged task {word}", story)
    second = await made(client, "task", f"Second paged task {word}", story)
    scope = f"#/board?epic={number_of(epic)}&show=both"
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        opening = {
            epic: lane(page, epic).locator("a.lane-title"),
            story: card(page, story).locator("h3.card-title a"),
            first: card(page, first).locator("h3.card-title a"),
        }
        for ref, link in opening.items():
            await w.open(scope)
            await expect(link).to_be_visible(timeout=SLOW)
            await expect(link).to_have_attribute("href", f"#/item?item={ref}&from=board")
            await link.click()
            # The page names the same item, is in the Board, and nothing opens beside it.
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", ref, timeout=SLOW
            )
            assert page.url.endswith(f"#/item?item={ref}&from=board")
            await expect(page.locator("#item-title")).to_contain_text(f"{code_of(ref)} ")
            await no_panel_or_drawer(page)
            assert (await crumbs(page))[2] == "Board"
            await expect(page.locator("#main-nav a[data-page=board]")).to_have_attribute(
                "aria-current", "page"
            )
            if ref == story:
                # What is under the story is what MCP says is under it, by number.
                wanted = [n["ref"] for n in (await ok(client, "list_nodes", parent=story))["items"]]
                assert wanted == [first, second]
                await expect(page.locator("#item-table tbody tr")).to_have_count(
                    len(wanted), timeout=SLOW
                )
                assert await item_rows(page) == wanted
            if ref == first:
                # A task has no table of what is under it: it is part of its story.
                assert await page.locator("#item-table").count() == 0
                await expect(page.locator("#item-part-title")).to_contain_text(code_of(story))
        assert w.problems == [], w.problems


async def test_the_notes_of_a_story_show_on_its_page_as_recall_says(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Known {word}")
    story = await made(client, "story", f"Story {word}", epic, target_on="2031-05-06")
    unconfirmed = await make_note(
        client, f"An unconfirmed note {word}", f"The call {word}", nodes=[story]
    )
    reviewed = await make_note(client, f"A reviewed note {word}", nodes=[story])
    confirm_on_board("note", reviewed, "Marie")
    gone = await make_note(client, f"A wrong note {word}", nodes=[story])
    await ok(client, "withdraw_note", note_ids=[gone["id"]], reason=f"It was wrong {word}")
    decision = await make_note(
        client,
        f"Use Postgres {word}",
        f"It is what we run {word}",
        decided_by="Ada",
        nodes=[story],
    )
    wide = await make_note(client, f"Always review {word}", decided_by="Marie", project_wide=True)
    recalled = await ok(client, "recall", node=story, limit=50)

    def held(section: str) -> list[int]:
        return [item["note"]["id"] for item in recalled[section]["items"]]

    # What binds comes first: the decision, then the plain notes by their numbers.
    assert held("notes") == [decision["id"], unconfirmed["id"], reviewed["id"]]
    assert held("past_notes") == [gone["id"]]
    assert wide["id"] in held("project_wide_notes")

    async with browser_page(board, token=the_token()) as w:
        await open_item(w, story)
        page = w.page
        summary = page.locator("#item-page .card-summary")
        await expect(summary).to_contain_text("Target")
        await expect(summary).to_contain_text("2031-05-06")
        context = page.locator("#item-page [data-part=context]")
        await expect(context.locator("[data-section=notes] tr.record-line")).to_have_count(3)

        async def shown(section: str) -> list[int]:
            rows = context.locator(f"[data-section={section}] tr.record-line")
            numbers = await rows.evaluate_all("els => els.map(e => Number(e.dataset.id))")
            return list(numbers)

        # Every section holds exactly what recall holds, and nothing else.
        for section in ("notes", "past_notes", "project_wide_notes"):
            assert await shown(section) == held(section), section
        # A section with fewer than five entries is open; with more it waits closed.
        for name in ("past_notes", "project_wide_notes"):
            folded = context.locator(f"details[data-disclosure={name}]")
            assert (await folded.get_attribute("open") is not None) == (
                recalled[name]["count"] < 5
            ), name

        # One status says where each note stands, in words and in a field, so an unconfirmed note is
        # never shown as confirmed, and a withdrawn one is visibly ended.
        row = context.locator(f"tr.record-line[data-ref='note:{unconfirmed['id']}']")
        await expect(row).to_have_attribute("data-status", "unconfirmed")
        await expect(row.locator(".chip.note-status")).to_have_text("Unconfirmed")
        await expect(row.locator("td.cell-decided")).to_have_text("")
        await expect(row.locator("td.cell-title")).to_contain_text(f"Why: The call {word}")
        row = context.locator(f"tr.record-line[data-ref='note:{reviewed['id']}']")
        await expect(row).to_have_attribute("data-status", "confirmed")
        chip = row.locator(".chip.note-status")
        await expect(chip).to_have_text("Confirmed")
        assert "Confirmed by Marie" in (await chip.get_attribute("title") or "")
        row = context.locator(f"tr.record-line[data-ref='note:{gone['id']}']")
        await expect(row).to_have_attribute("data-status", "withdrawn")
        await expect(row.locator(".chip.note-status")).to_have_text("Withdrawn")
        row = context.locator(f"tr.record-line[data-ref='note:{decision['id']}']")
        await expect(row.locator("td.cell-decided")).to_contain_text("Ada")
        await expect(row.locator("td.cell-title")).to_contain_text(f"Use Postgres {word}")
        row = context.locator(f"tr.record-line[data-ref='note:{wide['id']}']")
        await expect(row).to_have_attribute("data-project-wide", "true")
        await expect(row.locator("td.cell-applies")).to_contain_text("Whole project")

        # A row opens the page of its note, which says what the table cannot: why it was
        # withdrawn, and why a note was written.
        withdrawn = context.locator(f"tr.record-line[data-ref='note:{gone['id']}']")
        past = context.locator("details[data-disclosure=past_notes]")
        if await past.get_attribute("open") is None:
            await past.locator("summary").click()
        await withdrawn.locator("a.record-title").click()
        await expect(page.locator("#item-page")).to_have_attribute(
            "data-ref", f"note:{gone['id']}", timeout=SLOW
        )
        await expect(page.locator("#item-page [data-part=lifecycle]")).to_contain_text(
            f"It was wrong {word}"
        )
        await page.go_back()
        await context.locator(
            f"tr.record-line[data-ref='note:{decision['id']}'] a.record-title"
        ).click()
        await expect(page.locator("#item-page [data-part=why]")).to_contain_text(
            f"It is what we run {word}"
        )
        assert w.problems == [], w.problems


# -- the work filter and the address of a view ---------------------------------------------------


async def twelve(client: Client) -> dict[str, str]:
    """One epic, three stories and eight tasks that differ in status, priority, who holds them,
    dates and what they wait for: twelve items."""
    word = unique_word()
    ids: dict[str, str] = {"epic": await made(client, "epic", f"Twelve {word}")}
    ids["s1"] = await made(client, "story", f"Billing {word}", ids["epic"], priority="high")
    ids["s2"] = await made(client, "story", f"Portal {word}", ids["epic"])
    ids["s3"] = await made(client, "story", f"Reports {word}", ids["epic"], priority="urgent")
    for name, story, priority, extra in (
        ("t1", "s1", "urgent", {}),
        ("t2", "s1", "high", {}),
        ("t3", "s1", "low", {}),
        ("t4", "s2", "high", {}),
        ("t5", "s2", "normal", {}),
        ("t6", "s2", "high", {"target_on": day(-2)}),
        ("t7", "s3", "normal", {}),
        ("t8", "s3", "high", {"start_on": day(1), "target_on": day(9)}),
    ):
        ids[name] = await made(
            client, "task", f"Task {name} {word}", ids[story], priority=priority, **extra
        )
    ids["ann"], ids["bob"] = f"ann-{word}", f"bob-{word}"
    async with connect_each(ids["ann"], ids["bob"]) as (by_ann, by_bob):
        await ok(by_ann, "claim_node", node=ids["t1"])
        await ok(by_bob, "claim_node", node=ids["t7"])
    await ok(client, "update_node", node=ids["t1"], expected_version=1, status="in_progress")
    await ok(client, "update_node", node=ids["t3"], expected_version=1, status="done")
    await ok(client, "update_node", node=ids["t4"], expected_version=1, status="blocked")
    await ok(client, "add_link", source=ids["t5"], target=ids["t2"], link_type="needs_first")
    return ids


async def test_the_board_filter_is_the_one_list_nodes_reads_and_a_bad_one_is_refused_alike(
    client: Client, board: str
) -> None:
    ids = await twelve(client)
    epic = number_of(ids["epic"])
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/board?q=epic:{epic}&show=both")
        everything = await all_nodes(client, query=f"epic:{epic}")
        assert len(everything) == 12
        cards = {n["ref"] for n in everything if n["kind"] != "epic"}
        await cards_are(page, cards)
        await expect(page.locator("section.lane")).to_have_count(1)

        # The filter an agent gives as `query`: the same cards, and the text kept in the address.
        text = (
            f"epic:{epic} kind:task status:ready,in_progress -claimed priority:high "
            f"OR epic:{epic} kind:story"
        )
        wanted = await all_nodes(client, query=text)
        assert 0 < len(wanted) < 12
        await apply_filter(page, text)
        stories = {n["ref"] for n in wanted if n["kind"] == "story"}
        assert stories == {ids["s1"], ids["s2"], ids["s3"]}
        await cards_are(page, {n["ref"] for n in wanted if n["kind"] != "epic"})
        assert f"q=epic:{epic}" in page.url and "OR" in page.url

        # Held, dated and waiting work: each term of the language, against list_nodes.
        for term in (
            f"claimed:{ids['ann']},{ids['bob']}",
            "is:overdue",
            "status:blocked",
            "no:target kind:task",
            "-claimed kind:task status:done",
            f"target:{day(-5)}..{day(10)} -kind:epic",
        ):
            wanted = await all_nodes(client, query=f"epic:{epic} {term}")
            await apply_filter(page, f"epic:{epic} {term}")
            await cards_are(page, {n["ref"] for n in wanted if n["kind"] != "epic"})

        # A query that cannot be read is refused in the words list_nodes uses, and the cards stay.
        await apply_filter(page, f"epic:{epic}")
        await cards_are(page, cards)
        bad = f"epic:{epic} colour:red"
        said = await refused(client, "list_nodes", query=bad)
        await apply_filter(page, bad)
        problem = page.locator("#filter-problem")
        await expect(problem).to_contain_text("there is no field called 'colour'", timeout=SLOW)
        plain(await problem.inner_text(), said)
        assert len(await page.locator(".card").all()) == 11
        assert w.problems == [], w.problems


async def test_the_lists_work_outline_keeps_the_nodes_the_same_filter_asks_for(
    client: Client, board: str
) -> None:
    ids = await twelve(client)
    epic = number_of(ids["epic"])
    text = f"epic:{epic} kind:task -claimed priority:high,urgent"
    wanted = {n["ref"] for n in await all_nodes(client, query=text)}
    assert 0 < len(wanted) < 10
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/lists?view=work&q={text.replace(' ', '+')}")
        rows = w.page.locator("#work-tree li[role=treeitem]")
        await expect(rows.first).to_be_visible(timeout=SLOW)
        keys = await rows.evaluate_all("els => els.map(e => e.dataset.key)")
        assert wanted <= set(keys)
        assert not {ids[name] for name in ("t1", "t7", "t3")} & set(keys)
        await expect(w.page.locator("#filter")).to_have_value(text)
        bad = f"epic:{epic} colour:red"
        said = await refused(client, "list_nodes", query=bad)
        await apply_filter(w.page, bad)
        await expect(w.page.locator("#filter-problem")).to_contain_text(
            "there is no field called 'colour'", timeout=SLOW
        )
        plain(await w.page.locator("#filter-problem").inner_text(), said)
        assert w.problems == [], w.problems


async def test_the_addresses_of_a_view_and_of_a_page_open_the_same_thing_in_a_second_browser(
    client: Client, board: str
) -> None:
    ids = await twelve(client)
    epic = number_of(ids["epic"])
    note = await make_note(client, f"Kept in the address {epic}", nodes=[ids["t2"]])
    text = f"epic:{epic} kind:task -claimed priority:high,urgent"
    wanted = {n["ref"] for n in await all_nodes(client, query=text) if n["kind"] != "epic"}
    async with browser_page(board, token=the_token()) as first:
        await first.open("#/board")
        await first.page.locator("#epic-filter").select_option(str(epic))
        await apply_filter(first.page, text)
        await cards_are(first.page, wanted)
        view = first.page.url
        assert f"epic={epic}" in view and "q=" in view and "item=" not in view
        await card(first.page, ids["t2"]).locator("h3.card-title a").click()
        await expect(first.page.locator("#item-title")).to_contain_text(f"{code_of(ids['t2'])} ")
        await expect(
            first.page.locator(f"#item-page tr.record-line[data-ref='note:{note['id']}']")
        ).to_be_visible(timeout=SLOW)
        page_address = first.page.url
        assert page_address.endswith(f"#/item?item={ids['t2']}&from=board")
        # The way back is the view as it was left, with its filter and its epic.
        back = first.page.locator("nav.crumbs .crumb-view a")
        assert await back.get_attribute("href") == view.split(board)[1]
        assert the_token() not in view and the_token() not in page_address
        before = database_footprint()

        # A second browser, which has never seen this view: it signs in and opens the address of
        # the view, and then the address of the page.
        async with browser_page(board, token=the_token(), name="Someone else") as second:
            await second.page.goto(view)
            await cards_are(second.page, wanted)
            assert await second.page.locator("#filter").input_value() == text
            assert await second.page.locator("#epic-filter").input_value() == str(epic)
            await second.page.goto(page_address)
            await second.page.reload()
            await expect(second.page.locator("#item-page")).to_have_attribute(
                "data-ref", ids["t2"], timeout=SLOW
            )
            await expect(second.page.locator("#item-title")).to_contain_text(
                f"{code_of(ids['t2'])} "
            )
            await expect(
                second.page.locator(f"#item-page tr.record-line[data-ref='note:{note['id']}']")
            ).to_be_visible(timeout=SLOW)
            assert (await crumbs(second.page))[2] == "Board"
            # It is still in the Board, but nothing in memory says which choices the view had, so
            # the way back is the plain Board.
            link = second.page.locator("nav.crumbs .crumb-view a")
            assert await link.get_attribute("href") == "#/board"
            assert second.problems == [], second.problems
        assert database_footprint() == before
        assert not [name for name in before[0].split(",") if "view" in name or "saved" in name]


# -- what the Board cannot be told: parentage and dates, refused in the server's words -----------


async def test_a_story_or_task_made_with_no_parent_is_refused_in_the_words_mcp_uses(
    server_url: str,
) -> None:
    owner = register()
    project = make_project(owner)
    word = unique_word()
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key) as w,
    ):
        await w.open("#/board")
        await expect(w.page.locator("#board-empty")).to_be_visible(timeout=SLOW)

        async def attempt(kind: str, title: str) -> str:
            """Fill the New item form with no parent to choose from, and give what it says."""
            await w.go("#/board")
            await on_page(w.page, "board")
            await w.page.locator("#new-item").click()
            dialog = w.page.locator("dialog.modal")
            await dialog.locator("#new-kind").select_option(kind)
            assert await dialog.locator("#new-parent option").count() == 0
            await dialog.locator("#new-title").fill(title)
            await dialog.get_by_role("button", name="Create").click()
            problem = dialog.locator("p.problem")
            await expect(problem).not_to_be_empty(timeout=SLOW)
            words = await problem.inner_text()
            await dialog.get_by_role("button", name="Cancel").click()
            return words

        # A story needs an epic, and the project has none; the words are the tool's own.
        said = await refused(agent, "create_node", kind="story", title="Orphan")
        plain(await attempt("story", f"Orphan story {word}"), said)
        epic = await create_on_board(w, "epic", f"The only epic {word}")
        # A task needs a story, and the epic has none.
        said = await refused(agent, "create_node", kind="task", title="Orphan")
        plain(await attempt("task", f"Orphan task {word}"), said)
        titles = {n["title"] for n in (await ok(agent, "list_nodes"))["items"]}
        assert titles == {f"The only epic {word}"}, (titles, epic)
        assert w.problems == [] or all("status of 400" in p for p in w.problems), w.problems


async def test_a_start_date_after_the_target_typed_on_the_page_is_refused_in_the_words_mcp_uses(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Dates {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Dated {word}", story, start_on=day(2), target_on=day(9))
    said = await refused(client, "update_node", node=task, expected_version=1, start_on=day(12))
    before = (await node(client, task), len(await history(client, task)))
    async with browser_page(board, token=the_token()) as w:
        await open_item(w, task)
        await open_editor(w.page)
        await w.page.locator("#edit-start").fill(day(12))
        await w.page.get_by_role("button", name="Save changes").click()
        problem = w.page.locator("#item-page form.edit .problem .refusal-text")
        await expect(problem).to_be_visible(timeout=SLOW)
        plain(await problem.inner_text(), said)
        assert "Work cannot start after" in await problem.inner_text()
        # What was typed is still there, and nothing changed.
        assert await w.page.locator("#edit-start").input_value() == day(12)
    assert (await node(client, task), len(await history(client, task))) == before
