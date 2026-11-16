"""M10 against the running containers: the Board, its cards and the shared detail panel, driven in
a real headless browser and checked against a real MCP client (see ./check.sh). What a person does
on the board is checked by reading the graph back through the tools; what an agent does through
the tools is checked by looking at the board."""

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
    database_footprint,
    http,
    made,
    plain,
)
from tests.helpers import (
    all_nodes,
    confirm,
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
)
from tests.world import SESSION_COOKIE, api, make_project, project_at, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SLOW = 15_000  # what the board may take to show an agent's work: the poll is every 5 seconds


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def node(client: Client, node_id: int) -> dict[str, Any]:
    return await ok(client, "get_node", node_id=node_id)


async def history(client: Client, node_id: int) -> list[dict[str, Any]]:
    return list((await ok(client, "node_history", node_id=node_id, limit=200))["items"])


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
        ("POST", f"{mine}/tool/update_node", {"actor": "x", "node_id": epic}),
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
    change = {"actor": "x", "node_id": epic, "expected_version": 1, "title": f"Changed {word}"}
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
        assert (await ok(client, "get_node", node_id=epic))["version"] == 1
    assert owner.call("POST", path, change).status == 200
    assert (await ok(client, "get_node", node_id=epic))["version"] == 2

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
            await w.page.locator("#top nav").count() == 0
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
        assert await w.page.locator("#top nav").count() == 0

        # The right password opens the project in the address, on the Board, as the account's name.
        await w.page.locator("#account-password").fill(owner.password)
        assert await submit(w, "#sign-in-submit") == 200
        await expect(w.page.locator("h1")).to_have_text("Board", timeout=SLOW)
        await expect(w.page.locator("#who")).to_contain_text(owner.name)
        await expect(w.page.locator("#account")).to_have_text(owner.name)
        await expect(w.page.locator("#project-switcher")).to_have_value(str(project.id))
        assert await w.page.evaluate("localStorage.getItem('vrum.actor')") == owner.name

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
        await expect(w.page.locator("#who")).to_contain_text(shown)
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
        assert await w.page.locator("#top nav").count() == 0
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
        await expect(
            cell(w.page, epic, "ready").locator(f".card[data-id='{story}']")
        ).to_be_visible()
        working = cell(w.page, epic, "in_progress").locator(f".card[data-id='{first}']")
        await expect(working).to_contain_text("Held by agent-2")
        waiting = cell(w.page, epic, "new").locator(f".card[data-id='{second}']")
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
        await expect(card(w.page, story)).to_contain_text("1 of 2 tasks done")
        await expect(card(w.page, other)).to_contain_text("0 of 0 tasks done")
        assert w.problems == [], w.problems


# -- 3. what a person does on the board is what the graph then holds -------------------------------


async def test_what_a_person_does_on_the_board_is_in_the_graph_with_their_name_and_the_versions(
    client: Client, board: str
) -> None:
    word = unique_word()
    async with browser_page(board, token=the_token(), name=None) as w:
        await w.open("#/board")
        # With no name chosen yet, changes are recorded under the account's own name, until the
        # person says otherwise.
        await expect(w.page.locator("#who")).to_contain_text(project_at(board).owner.name)
        await w.page.locator("#who").click()
        who = w.page.locator("dialog.modal #name")
        await expect(who).to_be_visible()
        await who.fill("Ada Lovelace")
        await w.page.locator("dialog.modal").get_by_role("button", name="Save").click()
        await expect(w.page.locator("#who")).to_contain_text("Ada Lovelace")

        epic = await create_in_browser(w.page, "epic", f"Portal {word}")
        story = await create_in_browser(w.page, "story", f"Sign in {word}", epic)
        other_story = await create_in_browser(w.page, "story", f"Profile {word}", epic)
        task = await create_in_browser(w.page, "task", f"Login form {word}", story)
        friend = await create_in_browser(w.page, "task", f"Logout {word}", story)

        await w.go(f"#/board?epic={epic}&sel=node:{task}")
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
            cell(w.page, epic, "in_progress").locator(f".card[data-id='{task}']")
        ).to_be_visible()
        await w.page.drag_and_drop(
            f".card[data-id='{task}']", f".cell[data-epic='{epic}'][data-status='done']"
        )
        await expect(cell(w.page, epic, "done").locator(f".card[data-id='{task}']")).to_be_visible()

        # Move it to the other story, then add a link to its sibling and take it away again. The
        # card shows it is done first, as a person would see before going on.
        await w.go(f"#/board?epic={epic}&sel=node:{task}")
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
        assert "panel=coverage" in w.page.url
        await expect(panel).to_contain_text(
            f"Requirements in no story ({report['unplaced_rule_count']})"
        )
        await expect(panel).to_contain_text(
            f"Stories with no requirement ({report['story_without_rules_count']})"
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
        # A requirement is a rule in the database and the tools, and a Requirement on the board.
        assert "Rules in no story" not in await panel.inner_text()
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
        await expect(
            cell(w.page, epic, "new").locator(f".card[data-id='{second}']")
        ).to_be_visible()
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
        await expect(
            cell(w.page, epic, "new").locator(f".card[data-id='{second}']")
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
    await ok(
        client, "add_link", actor="a", source_id=second, target_id=first, link_type="needs_first"
    )
    before = (await ok(client, "list_links", node_id=first))["items"]
    said = await refused(
        client, "add_link", actor="x", source_id=first, target_id=second, link_type="needs_first"
    )

    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}&sel=node:{first}")
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
        await w.open(f"#/board?epic={epic}&sel=node:{story}")
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
        await w.open(f"#/board?epic={epic}&sel=node:{story}")
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
        await w.open(f"#/board?epic={epic}&sel=node:{story}")
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
    fact = await make_fact(
        client,
        f'<img src=x onerror="window.__ran=4"> a fact {word}',
        source=f"<b>the source</b> {word}",
        node_ids=[story],
    )
    decision = await make_decision(
        client,
        f"<u>A decision</u> {word}",
        text="<script>window.__ran=5</script> what was decided",
        reason='<img src=x onerror="window.__ran=6"> why',
        node_ids=[story],
    )

    status, headers, _ = http("GET", board)
    policy = headers["content-security-policy"]
    assert status == 200 and "default-src 'none'" in policy and "script-src 'self'" in policy
    assert "'unsafe-inline'" not in policy and "http" not in policy
    answered = board_call(board, "GET", "/board/api/info")
    assert answered.status == 200 and answered.headers["content-security-policy"] == policy

    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}")
        await expect(w.page.locator(f"section.lane[data-epic='{epic}']")).to_contain_text(markup)
        await w.page.locator(".lane-title").click()
        await expect(w.page.locator("#panel .body-text")).to_contain_text("<b>bold</b> <script>")
        await card(w.page, story).click()
        await expect(w.page.locator("#card-title")).to_contain_text(f"<i>{word}</i>")
        # What concerns the story is shown as text too: in its card, in the panel of each record,
        # in the search drawer and in the activity drawer.
        context = w.page.locator("#panel [data-part=context]")
        await expect(context.locator("li.record[data-kind=fact]")).to_contain_text(
            f'<img src=x onerror="window.__ran=4"> a fact {word}'
        )
        # Other tests may have made project-wide decisions, which every recall shows too.
        await expect(
            context.locator("li.record[data-kind=decision][data-project-wide=false]")
        ).to_contain_text("<script>window.__ran=5</script> what was decided")
        await w.go(f"#/board?epic={epic}&sel=fact:{fact['id']}")
        await expect(w.page.locator("#panel .record-detail .source")).to_contain_text(
            f"<b>the source</b> {word}"
        )
        await w.go(f"#/board?epic={epic}&sel=decision:{decision['id']}")
        await expect(w.page.locator("#panel .record-detail .reason")).to_contain_text(
            '<img src=x onerror="window.__ran=6"> why'
        )
        await w.go(f"#/board?epic={epic}&drawer=search&search={word}&search_mode=words")
        await expect(w.page.locator("#search-results li.result")).to_have_count(4, timeout=SLOW)
        await expect(w.page.locator("#search-results")).to_contain_text("<img src=x")
        await w.go(f"#/board?epic={epic}&drawer=activity")
        await expect(w.page.locator("#feed")).to_contain_text(f"a fact {word}", timeout=SLOW)
        await w.go(f"#/graph?epic={epic}")
        await expect(w.page.locator(f".dot[data-key='node:{epic}']")).to_be_visible()

        assert await w.page.evaluate("typeof window.__ran") == "undefined"
        assert await w.page.locator("img[src='x'], script:not([src]), b, u").count() == 0
        assert w.dialogs == [] and w.problems == [], (w.dialogs, w.problems)
        assert w.requests and all(url.startswith(w.origin) for url in w.requests), w.requests


# -- the keyboard -----------------------------------


async def test_a_card_can_be_opened_closed_and_moved_from_the_keyboard_alone(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Keys {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Keyed task {word}", story)
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}")
        target = card(w.page, task)
        await expect(target).to_be_visible()
        await target.focus()
        await w.page.keyboard.press("Enter")
        await expect(w.page.locator("#card-title")).to_contain_text(f"#{task} ")
        assert f"sel=node:{task}" in w.page.url
        await expect(w.page.locator("#card-title")).to_be_focused()

        # The panel closes with the keyboard too, and the card has the focus again.
        await w.page.keyboard.press("Tab")
        await expect(w.page.get_by_role("button", name="Close the details")).to_be_focused()
        await w.page.keyboard.press("Enter")
        await expect(w.page.locator("#panel")).to_be_hidden()
        assert "sel=" not in w.page.url
        await expect(target).to_be_focused()
        await w.page.keyboard.press("Space")
        await expect(w.page.locator("#card-title")).to_contain_text(f"#{task} ")

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
            cell(w.page, epic, "in_progress").locator(f".card[data-id='{task}']")
        ).to_be_visible()
        assert (await node(client, task))["status"] == "in_progress"
        assert w.problems == [], w.problems


async def test_the_facts_and_decisions_of_a_story_show_in_its_card_as_recall_says(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Known {word}")
    story = await made(client, "story", f"Story {word}", epic, target_on="2031-05-06")
    unconfirmed = await make_fact(
        client, f"An unconfirmed fact {word}", source=f"The call {word}", node_ids=[story]
    )
    reviewed = await confirm(
        client,
        "fact",
        await make_fact(client, f"A reviewed fact {word}", node_ids=[story]),
        person="Marie",
    )
    gone = await make_fact(client, f"A wrong fact {word}", node_ids=[story])
    await ok(
        client,
        "withdraw_fact",
        actor="agent-1",
        fact_ids=[gone["id"]],
        reason=f"It was wrong {word}",
    )
    decision = await make_decision(
        client,
        f"Use Postgres {word}",
        reason=f"It is what we run {word}",
        decided_by="Ada",
        node_ids=[story],
    )
    wide = await make_decision(client, f"Always review {word}", project_wide=True)
    recalled = await ok(client, "recall", node_id=story, limit=50)

    def held(section: str) -> list[int]:
        return [(item.get("fact") or item["decision"])["id"] for item in recalled[section]["items"]]

    assert held("facts") == [unconfirmed["id"], reviewed["id"]]
    assert held("past_facts") == [gone["id"]] and held("active_decisions") == [decision["id"]]
    assert wide["id"] in held("project_wide_decisions")

    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}&sel=node:{story}")
        detail = w.page.locator("#panel .card-detail")
        await expect(detail).to_contain_text("Target 2031-05-06")
        await expect(detail.locator("[data-section=facts] li.record")).to_have_count(2)

        async def shown(section: str) -> list[int]:
            rows = detail.locator(f"[data-section={section}] li.record")
            numbers = await rows.evaluate_all(
                "els => els.map(e => Number(e.dataset.record.split(':')[1]))"
            )
            return list(numbers)

        # Every section holds exactly what recall holds, and nothing else.
        for section in ("facts", "past_facts", "active_decisions", "project_wide_decisions"):
            assert await shown(section) == held(section), section

        # Confirmation and lifecycle are in words and fields, so an unconfirmed fact is never
        # shown as confirmed, and a withdrawn one is visibly ended, with its reason.
        row = detail.locator(f"li.record[data-record='fact:{unconfirmed['id']}']")
        await expect(row).to_have_attribute("data-confirmation", "unconfirmed")
        await expect(row).to_contain_text("Not confirmed")
        await expect(row).to_contain_text(f"Source: The call {word}")
        row = detail.locator(f"li.record[data-record='fact:{reviewed['id']}']")
        await expect(row).to_have_attribute("data-confirmation", "confirmed")
        await expect(row).to_contain_text("Confirmed by Marie")
        row = detail.locator(f"li.record[data-record='fact:{gone['id']}']")
        await expect(row).to_have_attribute("data-status", "withdrawn")
        await expect(row).to_contain_text(f"It was wrong {word}")
        row = detail.locator(f"li.record[data-record='decision:{decision['id']}']")
        await expect(row).to_contain_text("Decided by Ada")
        await expect(row).to_contain_text(f"It is what we run {word}")
        row = detail.locator(f"li.record[data-record='decision:{wide['id']}']")
        await expect(row).to_have_attribute("data-project-wide", "true")
        await expect(row).to_contain_text("Project-wide")
        assert w.problems == [], w.problems


# -- the work filter and the address of a view ---------------------------------------------------


async def twelve(client: Client) -> dict[str, int]:
    """One epic, three stories and eight tasks that differ in status, priority, who holds them,
    dates and what they wait for: twelve items."""
    word = unique_word()
    ids: dict[str, int] = {"epic": await made(client, "epic", f"Twelve {word}")}
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
    await ok(client, "claim_node", actor="ann", node_id=ids["t1"])
    await ok(
        client,
        "update_node",
        actor="a",
        node_id=ids["t1"],
        expected_version=1,
        status="in_progress",
    )
    await ok(client, "update_node", actor="a", node_id=ids["t3"], expected_version=1, status="done")
    await ok(
        client, "update_node", actor="a", node_id=ids["t4"], expected_version=1, status="blocked"
    )
    await ok(client, "claim_node", actor="bob", node_id=ids["t7"])
    await ok(
        client,
        "add_link",
        actor="a",
        source_id=ids["t5"],
        target_id=ids["t2"],
        link_type="needs_first",
    )
    return ids


async def test_the_board_filter_is_the_one_list_nodes_reads_and_a_bad_one_is_refused_alike(
    client: Client, board: str
) -> None:
    ids = await twelve(client)
    epic = ids["epic"]
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/board?q=epic:{epic}")
        everything = await all_nodes(client, query=f"epic:{epic}")
        assert len(everything) == 12
        cards = {n["id"] for n in everything if n["kind"] != "epic"}
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
        stories = {n["id"] for n in wanted if n["kind"] == "story"}
        assert stories == {ids["s1"], ids["s2"], ids["s3"]}
        await cards_are(page, {n["id"] for n in wanted if n["kind"] != "epic"})
        assert f"q=epic:{epic}" in page.url and "OR" in page.url

        # Held, dated and waiting work: each term of the language, against list_nodes.
        for term in (
            "claimed:ann,bob",
            "is:overdue",
            "status:blocked",
            "no:target kind:task",
            "-claimed kind:task status:done",
            f"target:{day(-5)}..{day(10)} -kind:epic",
        ):
            wanted = await all_nodes(client, query=f"epic:{epic} {term}")
            await apply_filter(page, f"epic:{epic} {term}")
            await cards_are(page, {n["id"] for n in wanted if n["kind"] != "epic"})

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


async def test_the_graph_work_list_keeps_the_nodes_the_same_filter_asks_for(
    client: Client, board: str
) -> None:
    ids = await twelve(client)
    epic = ids["epic"]
    text = f"epic:{epic} kind:task -claimed priority:high,urgent"
    wanted = {n["id"] for n in await all_nodes(client, query=text)}
    assert 0 < len(wanted) < 10
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/graph?view=work&as=list&q={text.replace(' ', '+')}")
        rows = w.page.locator("#work-tree li[role=treeitem]")
        await expect(rows.first).to_be_visible(timeout=SLOW)
        keys = await rows.evaluate_all("els => els.map(e => e.dataset.key)")
        assert {f"node:{n}" for n in wanted} <= set(keys)
        assert not {f"node:{ids[name]}" for name in ("t1", "t7", "t3")} & set(keys)
        await expect(w.page.locator("#filter")).to_have_value(text)
        bad = f"epic:{epic} colour:red"
        said = await refused(client, "list_nodes", query=bad)
        await apply_filter(w.page, bad)
        await expect(w.page.locator("#filter-problem")).to_contain_text(
            "there is no field called 'colour'", timeout=SLOW
        )
        plain(await w.page.locator("#filter-problem").inner_text(), said)
        assert w.problems == [], w.problems


async def test_a_boards_address_opens_the_same_view_in_a_second_browser_and_stores_nothing(
    client: Client, board: str
) -> None:
    ids = await twelve(client)
    epic = ids["epic"]
    fact = await make_fact(client, f"Kept in the address {epic}", node_ids=[ids["t2"]])
    text = f"epic:{epic} kind:task -claimed priority:high,urgent"
    wanted = {n["id"] for n in await all_nodes(client, query=text) if n["kind"] != "epic"}
    async with browser_page(board, token=the_token()) as first:
        await first.open("#/board")
        await first.page.locator("#epic-filter").select_option(str(epic))
        await apply_filter(first.page, text)
        await cards_are(first.page, wanted)
        await card(first.page, ids["t2"]).click()
        await expect(first.page.locator("#card-title")).to_contain_text(f"#{ids['t2']} ")
        await expect(
            first.page.locator(f"#panel li.record[data-record='fact:{fact['id']}']")
        ).to_be_visible()
        address = first.page.url
        assert f"epic={epic}" in address and f"sel=node:{ids['t2']}" in address and "q=" in address
        assert the_token() not in address
        before = database_footprint()

        # A second browser, which has never seen this view: it signs in and opens the address.
        async with browser_page(board, token=the_token(), name="Someone else") as second:
            await second.page.goto(address)
            await cards_are(second.page, wanted)
            assert await second.page.locator("#filter").input_value() == text
            assert await second.page.locator("#epic-filter").input_value() == str(epic)
            await expect(second.page.locator("#card-title")).to_contain_text(
                f"#{ids['t2']} ", timeout=SLOW
            )
            await expect(
                second.page.locator(f"#panel li.record[data-record='fact:{fact['id']}']")
            ).to_be_visible()
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
        said = await refused(agent, "create_node", actor="x", kind="story", title="Orphan")
        plain(await attempt("story", f"Orphan story {word}"), said)
        epic = await create_in_browser(w.page, "epic", f"The only epic {word}")
        # A task needs a story, and the epic has none.
        said = await refused(agent, "create_node", actor="x", kind="task", title="Orphan")
        plain(await attempt("task", f"Orphan task {word}"), said)
        titles = {n["title"] for n in (await ok(agent, "list_nodes"))["items"]}
        assert titles == {f"The only epic {word}"}, (titles, epic)
        assert w.problems == [] or all("status of 400" in p for p in w.problems), w.problems


async def test_a_start_date_after_the_target_typed_in_the_card_is_refused_in_the_words_mcp_uses(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Dates {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Dated {word}", story, start_on=day(2), target_on=day(9))
    said = await refused(
        client, "update_node", actor="x", node_id=task, expected_version=1, start_on=day(12)
    )
    before = (await node(client, task), len(await history(client, task)))
    async with browser_page(board, token=the_token()) as w:
        await w.open(f"#/board?epic={epic}&sel=node:{task}")
        await w.page.locator("#edit-start").fill(day(12))
        await w.page.get_by_role("button", name="Save changes").click()
        problem = w.page.locator("#panel .edit .problem .refusal-text")
        await expect(problem).to_be_visible(timeout=SLOW)
        plain(await problem.inner_text(), said)
        assert "Work cannot start after" in await problem.inner_text()
        # What was typed is still there, and nothing changed.
        assert await w.page.locator("#edit-start").input_value() == day(12)
    assert (await node(client, task), len(await history(client, task))) == before
