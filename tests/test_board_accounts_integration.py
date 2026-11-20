"""Accounts and projects in a real browser (M15): registering, signing in and out, creating
projects and working in them, switching between them, another person's project address, and making
and revoking an agent key, each compared with what a real MCP client sees of the same project."""

import asyncio
import contextlib
import json
import re
import time
import urllib.error
import urllib.request

import pytest
from playwright.async_api import expect

from tests.board_helpers import SLOW, browser_page, create_in_browser, made
from tests.helpers import compose, connect, ok, refused, unique_word
from tests.world import api, make_project, register, server_base
from vrum.surface import AGENT_TOOLS

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

PASSWORD = "a long enough password"


def wait_until_healthy(base: str) -> None:
    for _ in range(120):
        with (
            contextlib.suppress(urllib.error.URLError, OSError),
            urllib.request.urlopen(f"{base}/health", timeout=3) as response,
        ):
            if response.status == 200:
                return
        time.sleep(0.5)
    pytest.fail("the server did not come back")


def messages(error: BaseException) -> str:
    inner = getattr(error, "exceptions", None)
    return " ".join([str(error)] + [messages(one) for one in inner or []])


async def register_in_the_browser(w, name: str, email: str, password: str) -> None:  # type: ignore[no-untyped-def]
    await w.open("")
    await expect(w.page.locator("#sign-in-form")).to_be_visible(timeout=SLOW)
    await w.page.locator("#show-register").click()
    await w.page.locator("#account-name").fill(name)
    await w.page.locator("#account-email").fill(email)
    await w.page.locator("#account-password").fill(password)
    await w.page.locator("#register-submit").click()


async def create_in_the_browser(w, kind: str, title: str, parent: str | None = None) -> str:  # type: ignore[no-untyped-def]
    """Make an item with the Board's New item form, which goes to the page of the new item."""
    await w.go("#/board")
    await expect(w.page.locator("#new-item")).to_be_visible(timeout=SLOW)
    ref = await create_in_browser(w.page, kind, title, parent)
    await expect(w.page.locator("#item-page")).to_have_attribute("data-ref", ref, timeout=SLOW)
    await expect(w.page.locator("nav.crumbs .crumb-view a")).to_have_text("Board")
    return ref


async def test_a_person_registers_makes_a_project_works_in_it_and_survives_a_restart() -> None:
    base = server_base()
    word = unique_word()
    email, name = f"{word}@example.test", f"Grace {word}"
    async with browser_page(f"{base}/board", token=None, name=None) as w:
        # Nobody is signed in: the sign-in page, and no token to type anywhere.
        await w.open("")
        await expect(w.page.locator("#sign-in-form")).to_be_visible(timeout=SLOW)
        assert await w.page.locator("#token").count() == 0
        await register_in_the_browser(w, name, email, PASSWORD)

        # A real empty state, and no project invented for the person.
        await expect(w.page.locator("#no-projects")).to_contain_text(
            "no projects yet", timeout=SLOW
        )
        await expect(w.page.locator("#account")).to_have_text(name)
        assert await w.page.locator("li.project").count() == 0

        await w.page.locator("#project-name").fill(f"Compiler {word}")
        await w.page.locator("#create-project").click()
        await expect(w.page.locator("#main-nav")).to_be_visible(timeout=SLOW)
        match = re.search(r"/board/p/(\d+)", w.page.url)
        assert match, w.page.url
        project_number = int(match.group(1))
        await expect(w.page.locator("#project-switcher option:checked")).to_have_text(
            f"Compiler {word}"
        )
        await expect(w.page.locator("h1")).to_have_text("Board", timeout=SLOW)
        # The Board is the landing view, so a first look at a new project needs no other page.
        assert w.page.url.endswith("#/board")

        # The board's own forms make an epic, a story under it and a task under that.
        await w.go("#/board")
        epic = await create_in_the_browser(w, "epic", f"Portal {word}")
        story = await create_in_the_browser(w, "story", f"Sign in {word}", epic)
        task = await create_in_the_browser(w, "task", f"Login form {word}", story)
        # Back on the Board, the new work is in its lanes and cards.
        await w.go("#/board")
        await expect(w.page.locator(f".card[data-ref='{task}']")).to_be_visible(timeout=SLOW)

        # An agent key made on the project's own Users page (the Settings menu) reaches the same
        # records over MCP.
        await w.page.locator("#settings-menu").click()
        await w.page.locator("#settings-users").click()
        await expect(w.page.locator("#key-name")).to_be_visible(timeout=SLOW)
        assert "#/users" in w.page.url
        await w.page.locator("#key-name").fill("build agent")
        await expect(w.page.locator("#create-key")).to_be_enabled(timeout=SLOW)
        await w.page.locator("#create-key").click()
        await expect(w.page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
        # A key made with no choice of tools is the standard one: the tools Vrum offers agents.
        await expect(w.page.locator("#new-key-allowed")).to_contain_text("Standard, 25 tools")
        key = (await w.page.locator("#key-secret").inner_text()).strip()
        async with connect(f"{base}/mcp", key) as agent:
            nodes = (await ok(agent, "list_nodes"))["items"]
            assert sorted(node["title"] for node in nodes) == sorted(
                [f"Portal {word}", f"Sign in {word}", f"Login form {word}"]
            )
            assert {node["ref"] for node in nodes} == {epic, story, task}

        # Sign out, and the old session is of no use; sign back in with the email and password.
        await w.page.locator("#sign-out").click()
        await expect(w.page.locator("#sign-in-form")).to_be_visible(timeout=SLOW)
        assert w.page.url.rstrip("/").endswith("/board")

        # The server restarts. Nothing is lost: the account, the project and the records are in the
        # database, and an agent's key still works.
        restarted = compose("restart", "app")
        assert restarted.returncode == 0, restarted.stderr
        wait_until_healthy(base)
        await w.page.reload()
        await expect(w.page.locator("#sign-in-form")).to_be_visible(timeout=SLOW)
        await w.page.locator("#account-email").fill(email)
        await w.page.locator("#account-password").fill(PASSWORD)
        await w.page.locator("#sign-in-submit").click()
        await expect(w.page.locator("li.project")).to_have_count(1, timeout=SLOW)
        await expect(w.page.locator("li.project a.open-project")).to_have_text(f"Compiler {word}")
        await w.page.locator("li.project a.open-project").click()
        await expect(w.page.locator("#main-nav")).to_be_visible(timeout=SLOW)
        assert f"/board/p/{project_number}" in w.page.url
        await w.go("#/board?show=both")
        for item in (story, task):
            await expect(w.page.locator(f".card[data-ref='{item}']").first).to_be_visible(
                timeout=SLOW
            )
        await expect(w.page.get_by_role("region", name=f"Epic Portal {word}")).to_be_visible(
            timeout=SLOW
        )
        async with connect(f"{base}/mcp", key) as agent:
            assert len((await ok(agent, "list_nodes"))["items"]) == 3
        # No password, session or key was kept in the page's storage or in an address.
        stored = await w.page.evaluate("JSON.stringify([localStorage, sessionStorage])")
        for secret in (PASSWORD, key):
            assert secret not in stored and secret not in w.page.url
            assert all(secret not in url for url in w.requests)
        assert await w.page.evaluate("document.cookie") == ""
        assert w.problems == [] or all("status of 4" in problem for problem in w.problems)


async def test_the_sign_in_and_registration_pages_refuse_in_plain_words_and_change_nothing() -> (
    None
):
    base = server_base()
    person = register()
    async with browser_page(f"{base}/board", token=None, name=None) as w:
        await w.open("")
        form = w.page.locator("#sign-in-form")
        await expect(form).to_be_visible(timeout=SLOW)

        # Nothing typed: the server's own words, the same as through the API.
        await w.page.locator("#sign-in-submit").click()
        await expect(w.page.locator("#entry-problem")).to_contain_text("not right", timeout=SLOW)
        await w.page.locator("#account-email").fill(person.email)
        await w.page.locator("#account-password").fill("not the password")
        await w.page.locator("#sign-in-submit").click()
        await expect(w.page.locator("#entry-problem")).to_contain_text(
            "email address or the password is not right", timeout=SLOW
        )
        assert "not the password" not in await w.page.locator("#entry-problem").inner_text()
        # What was typed stays, and the person is still not signed in.
        await expect(w.page.locator("#account-email")).to_have_value(person.email)
        assert await w.page.locator("#account").count() == 0

        # Registering an address that has an account is refused and the account is untouched.
        await w.page.locator("#show-register").click()
        await w.page.locator("#account-name").fill("Someone else")
        await w.page.locator("#account-email").fill(person.email.upper())
        await w.page.locator("#account-password").fill("another long password")
        await w.page.locator("#register-submit").click()
        await expect(w.page.locator("#entry-problem")).to_contain_text(
            "already exists", timeout=SLOW
        )
        await w.page.locator("#account-email").fill(f"{unique_word()}@example.test")
        await w.page.locator("#account-password").fill("short")
        await w.page.locator("#register-submit").click()
        await expect(w.page.locator("#entry-problem")).to_contain_text(
            "10 to 200 characters", timeout=SLOW
        )
        signed = person.sign_in_again()
        assert signed.name == person.name

        # The cookie that signs the person in is out of reach of the page's scripts.
        await w.page.locator("#show-sign-in").click()
        await w.page.locator("#account-email").fill(person.email)
        await w.page.locator("#account-password").fill(person.password)
        await w.page.locator("#sign-in-submit").click()
        await expect(w.page.locator("#no-projects")).to_be_visible(timeout=SLOW)
        assert await w.page.evaluate("document.cookie") == ""
        cookies = await w.page.context.cookies()
        session = next(one for one in cookies if one["name"] == "vrum_session")
        assert session["httpOnly"] is True and session["sameSite"] == "Strict"
        assert session["path"] == "/board"


async def test_a_person_sees_only_their_own_projects_and_anothers_address_opens_nothing() -> None:
    base = server_base()
    word = unique_word()
    ann, bob = register(name=f"Ann {word}"), register(name=f"Bob {word}")
    theirs = make_project(ann, f"Anns {word}")
    mine = make_project(bob, f"Bobs {word}")
    make_project(bob, f"Bobs second {word}")
    async with connect(theirs.mcp_url, theirs.key) as agent:
        await made(agent, "epic", f"Private to Ann {word}")

    async with browser_page(mine.address, token=mine.key, name=None) as w:
        await w.open("#/board")
        options = await w.page.locator("#project-switcher option").all_inner_texts()
        assert options == [f"Bobs {word}", f"Bobs second {word}", "All projects…"]
        assert f"Anns {word}" not in await w.page.locator("#app").inner_text()

        # Ann's project address, copied to Bob's browser, opens nothing of hers.
        await w.page.goto(theirs.address)
        await expect(w.page.locator("#no-such-project")).to_contain_text(
            "no project with that address among yours", timeout=SLOW
        )
        assert f"Private to Ann {word}" not in await w.page.locator("body").inner_text()
        assert all(f"Private to Ann {word}" not in text for _, text in w.bodies)
        for number in (theirs.id, theirs.id + 100_000):
            assert f"/projects/{number}/tool/" not in " ".join(w.requests)
        await w.page.locator("#back-to-projects").click()
        await expect(w.page.locator("li.project")).to_have_count(2, timeout=SLOW)
        names = await w.page.locator("li.project a.open-project").all_inner_texts()
        assert names == [f"Bobs {word}", f"Bobs second {word}"]
    # The same address for a person who is not signed in at all asks them to sign in.
    async with browser_page(theirs.address, token=None, name=None) as w:
        await w.open("")
        await expect(w.page.locator("#sign-in-form")).to_be_visible(timeout=SLOW)
        assert f"Private to Ann {word}" not in await w.page.locator("body").inner_text()
    assert base in theirs.address


async def test_switching_projects_with_responses_held_back_never_shows_the_first_ones_records() -> (
    None
):
    word = unique_word()
    owner = register(name=f"Switcher {word}")
    first = make_project(owner, f"First {word}")
    second = make_project(owner, f"Second {word}")
    async with (
        connect(first.mcp_url, first.key) as one,
        connect(second.mcp_url, second.key) as two,
    ):
        await made(one, "epic", f"Only in the first {word}")
        await made(two, "epic", f"Only in the second {word}")

        async with browser_page(first.address, token=first.key, name=None, keep_bodies=True) as w:
            await w.open("#/board")
            await expect(
                w.page.get_by_role("region", name=f"Epic Only in the first {word}")
            ).to_be_visible(timeout=SLOW)
            # From here on, the first project's answers (the change feed and the lists) are held
            # back, and an agent changes the first project so that there is news to hold back.
            released = asyncio.Event()
            held: list[str] = []

            async def hold(route):  # type: ignore[no-untyped-def]
                held.append(route.request.url)
                await released.wait()
                with contextlib.suppress(Exception):
                    await route.continue_()

            await w.page.route(f"**/board/api/projects/{first.id}/tool/**", hold)
            await made(one, "epic", f"News for the first {word}")
            for _ in range(200):
                if held:
                    break
                await asyncio.sleep(0.1)
            assert held, "no poll of the first project was held back"

            # Switch to the second project from the sidebar while they are held back.
            before = len(w.requests)
            await w.page.locator("#project-switcher").select_option(label=f"Second {word}")
            await expect(w.page.locator("#project-switcher option:checked")).to_have_text(
                f"Second {word}", timeout=SLOW
            )
            assert f"/board/p/{second.id}" in w.page.url
            await w.go("#/board")
            await expect(
                w.page.get_by_role("region", name=f"Epic Only in the second {word}")
            ).to_be_visible(timeout=SLOW)
            # Release what was held, and give the page time to wrongly draw it.
            released.set()
            await asyncio.sleep(2)
            shown = await w.page.locator("body").inner_text()
            assert f"Only in the first {word}" not in shown
            assert f"News for the first {word}" not in shown
            assert f"Only in the second {word}" in shown
            # After the switch the page asked only about the second project.
            later = w.requests[before:]
            assert not [url for url in later if f"/projects/{first.id}/" in url], later
            # No response body the second project's page received held the first's records.
            after = [text for url, text in w.bodies if f"/projects/{second.id}/" in url]
            assert after and all(f"Only in the first {word}" not in text for text in after)


async def test_an_agent_key_made_on_the_board_works_for_that_project_only_until_it_is_revoked() -> (
    None
):
    word = unique_word()
    owner = register(name=f"Keeper {word}")
    project = make_project(owner, f"Keyed {word}")
    other = make_project(owner, f"Other {word}")
    async with connect(other.mcp_url, other.key) as elsewhere:
        await made(elsewhere, "epic", f"Elsewhere {word}")

    async with browser_page(project.address, token=project.key, name=None) as w:
        await w.open("#/users")
        await expect(w.page.locator("#keys li.key")).to_have_count(1, timeout=SLOW)
        await w.page.locator("#key-name").fill(f"second agent {word}")
        await expect(w.page.locator("#create-key")).to_be_enabled(timeout=SLOW)
        await w.page.locator("#create-key").click()
        await expect(w.page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
        secret = (await w.page.locator("#key-secret").inner_text()).strip()
        await expect(w.page.locator("#keys li.key")).to_have_count(2, timeout=SLOW)
        # The key is shown once: after leaving the page it is not anywhere on it.
        await w.go("#/board")
        await w.go("#/users")
        await expect(w.page.locator("#keys li.key")).to_have_count(2, timeout=SLOW)
        assert secret not in await w.page.locator("body").inner_text()
        row = w.page.locator("#keys li.key", has_text=f"second agent {word}")
        await expect(row).to_contain_text(f"{secret[:8]}…")
        await expect(row.locator(".key-allowance")).to_have_text("Standard, 25 tools")

        # A real MCP client with the key does a day's work here, and cannot see the other project.
        async with connect(project.mcp_url, secret) as agent:
            task = await made(agent, "epic", f"Work {word}")
            story = await ok(
                agent,
                "create_node",
                kind="story",
                title=f"A story {word}",
                parent=task,
            )
            taken = await ok(agent, "next_work")
            assert taken["work"]["ref"] == story["ref"] and taken["claim"] is not None
            await ok(
                agent,
                "finish_work",
                node=story["ref"],
                outcome="done",
                note="Done with the key from the board.",
            )
            titles = json.dumps(await ok(agent, "list_nodes", limit=200))
            assert f"Elsewhere {word}" not in titles and f"Work {word}" in titles
            await expect(row).to_contain_text("last used", timeout=SLOW)

            # Revoke it on the board: the open connection and a new one are refused at once.
            await row.locator("button.revoke").click()
            await expect(w.page.locator("dialog.modal")).to_contain_text(
                "refused on its next request"
            )
            await w.page.locator("dialog.modal button.primary").click()
            await expect(w.page.locator("#keys li.key[data-state=revoked]")).to_have_count(
                1, timeout=SLOW
            )
            with pytest.raises(BaseException) as open_connection:
                await agent.call_tool("list_nodes", {})
            assert "agent key is not valid" in messages(open_connection.value)
        with pytest.raises(BaseException) as new_connection:
            async with connect(project.mcp_url, secret) as again:
                await again.call_tool("list_nodes", {})
        assert "agent key is not valid" in messages(new_connection.value)
        # The project's first key, and the other project, are as they were.
        async with connect(project.mcp_url, project.key) as agent:
            assert f"Work {word}" in json.dumps(await ok(agent, "list_nodes", limit=200))


async def make_key_on_the_page(w, name: str, choice: str | None = None) -> str:  # type: ignore[no-untyped-def]
    """Make an agent key on the Users page, with one of the quick choices of what it may do (the
    standard tools when none is chosen), and give the key that is shown once."""
    await w.go("#/users")
    await expect(w.page.locator("#key-name")).to_be_visible(timeout=SLOW)
    await expect(w.page.locator("#create-key")).to_be_enabled(timeout=SLOW)
    if choice is not None:
        await w.page.locator(f"#key-tools-choice-{choice}").click()
        await expect(w.page.locator(f"#key-tools-choice-{choice}")).to_have_attribute(
            "aria-checked", "true"
        )
    await w.page.locator("#key-name").fill(name)
    await w.page.locator("#create-key").click()
    await expect(w.page.locator("#new-key-title")).to_contain_text(name, timeout=SLOW)
    await expect(w.page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
    return str((await w.page.locator("#key-secret").inner_text()).strip())


async def test_the_key_picker_makes_standard_read_only_and_everything_keys_the_server_holds() -> (
    None
):
    word = unique_word()
    owner = register(name=f"Picker {word}")
    project = make_project(owner, f"Picked {word}")
    listing = owner.call("GET", "/board/api/tools")
    assert listing.status == 200
    tools = listing.body["tools"]
    offered = {t["name"] for t in tools if t["offered_to_agents"]}
    assert offered == set(AGENT_TOOLS) and listing.body["most_agent_tools"] == len(offered) == 25
    readers = {t["name"] for t in tools if t["offered_to_agents"] and not t["changes_something"]}
    grantable = {t["name"] for t in tools if not t["people_only"]}
    assert len(grantable) == len(tools) - 2

    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await w.open("#/users")
        await expect(page.locator("#create-key")).to_be_enabled(timeout=SLOW)
        # A new key starts as the standard one, which sends no list of tools of its own; the
        # tools that are not offered to agents by default are in a fold that starts closed.
        await expect(page.locator("#key-tools-choice-standard")).to_have_attribute(
            "aria-checked", "true"
        )
        await expect(page.locator("#key-tools-choice-everything")).to_be_visible()
        await page.locator("#key-tools-toggle").click()
        fold = page.locator("#key-tools-not-offered")
        await expect(fold).to_be_hidden()
        await page.locator(".kt-group-other .kt-fold-named").click()
        await expect(fold).to_be_visible()
        await expect(fold.locator("li.kt-tool[data-offered='0']")).to_have_count(len(tools) - 25)
        await expect(page.locator("li.kt-tool[data-offered='1']")).to_have_count(25)
        await page.locator("#key-tools-toggle").click()

        # Standard: exactly the 25 tools, and any other is refused in words that say it is done
        # on the board.
        standard = await make_key_on_the_page(w, f"standard-{word}")
        row = page.locator("#keys li.key", has_text=f"standard-{word}")
        await expect(row.locator(".key-allowance")).to_have_text("Standard, 25 tools")
        await expect(row).to_have_attribute("data-state", "active")
        await expect(row.locator(".key-allowance")).to_have_attribute("data-allowance", "standard")
        async with connect(project.mcp_url, standard) as agent:
            assert {t.name for t in (await agent.list_tools()).tools} == set(AGENT_TOOLS)
            said = await refused(agent, "server_status")
            assert "is not one of the tools Vrum offers agents" in said
            assert "done on the board, by a person" in said

        # Read only: the tools that only read, and nothing that changes anything.
        read = await make_key_on_the_page(w, f"read-{word}", "read")
        row = page.locator("#keys li.key", has_text=f"read-{word}")
        await expect(row.locator(".key-allowance")).to_have_attribute("data-allowance", "read")
        async with connect(project.mcp_url, read) as agent:
            assert {t.name for t in (await agent.list_tools()).tools} == readers
            said = await refused(agent, "create_node", kind="epic", title="Not allowed")
            assert "is not allowed to use create_node" in said
            assert (await ok(agent, "list_nodes", limit=1))["total"] == 0

        # Everything, including administration: every tool a key can have, as a list of its own
        # (so a tool added later is not in it), and the one only people may use is not.
        everything = await make_key_on_the_page(w, f"admin-{word}", "everything")
        row = page.locator("#keys li.key", has_text=f"admin-{word}")
        await expect(row.locator(".key-allowance")).to_have_attribute(
            "data-allowance", "everything"
        )
        await expect(row.locator(".key-allowance")).to_have_text(
            f"Everything, {len(grantable)} tools"
        )
        async with connect(project.mcp_url, everything) as agent:
            assert {t.name for t in (await agent.list_tools()).tools} == grantable
            assert (await ok(agent, "server_status"))["counts"]["epics"] == 0
            assert (await ok(agent, "export_graph", limit=1))["format"] == "vrum-export"
        assert w.problems == []


async def test_a_session_that_ended_sends_the_page_back_to_sign_in_and_signing_in_returns() -> None:
    word = unique_word()
    owner = register(name=f"Timed {word}")
    project = make_project(owner, f"Timed {word}")
    async with browser_page(project.address, token=project.key, name=None) as w:
        await w.open("#/board")
        await expect(w.page.locator("#account")).to_have_text(f"Timed {word}", timeout=SLOW)
        # The server ends the session (as signing out in another tab would).
        cookies = await w.page.context.cookies()
        session = next(one for one in cookies if one["name"] == "vrum_session")["value"]
        # Ending a session needs the page's own secret: a bare copy of the cookie cannot do it.
        assert api(owner.base, "DELETE", "/board/api/session", cookie=session).status == 403
        ended = compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "vrum",
            "-d",
            "vrum",
            "-c",
            "UPDATE sessions SET ended_at = now() WHERE ended_at IS NULL AND account_id = "
            f"{owner.id}",
        )
        assert ended.returncode == 0, ended.stderr
        # The next look at the server's news is refused, and the page says so and asks to sign in.
        await expect(w.page.locator("#sign-in-form")).to_be_visible(timeout=15_000)
        await expect(w.page.locator("#entry-problem")).to_contain_text(
            "session has ended", timeout=SLOW
        )
        await w.page.locator("#account-email").fill(owner.email)
        await w.page.locator("#account-password").fill(owner.password)
        await w.page.locator("#sign-in-submit").click()
        await expect(w.page.locator("#main-nav")).to_be_visible(timeout=SLOW)
        assert f"/board/p/{project.id}" in w.page.url


async def test_what_sticks_under_the_top_bar_starts_where_the_bar_really_ends() -> None:
    word = unique_word()
    owner = register(name=f"A person with quite a long name {word}")
    project = make_project(owner, f"A project with a long name too {word}")
    long_title = (
        f"Under the bar, with a title that is a good deal longer than the bar is wide {word}"
    )
    # What sticks under the bar on a page (the section list of a record's page, the side column of
    # the Activity page) starts below where the bar really ends: it is published as --top-now.
    agreed = """(selector) => {
        const near = (a, b) => Math.abs(a - b) < 0.5;
        const bar = document.getElementById('top').getBoundingClientRect().height;
        const said = getComputedStyle(document.documentElement).getPropertyValue('--top-now');
        const column = document.querySelector(selector);
        if (!column || !near(parseFloat(said), bar)) return false;
        const style = getComputedStyle(column);
        return style.position === 'sticky' && parseFloat(style.top) >= bar - 0.5;
    }"""
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic = await made(agent, "epic", long_title)
        await w.open(f"#/item?item={epic}&from=board")
        await expect(w.page.locator("#item-page")).to_have_attribute("data-ref", epic, timeout=SLOW)
        await expect(w.page.locator("#main-nav")).to_be_visible(timeout=SLOW)
        # The long names (the person's, the project's and the item's, which are in the sidebar and
        # the path) are cut short instead of making the bar taller, and what sticks starts below it.
        await w.page.wait_for_function(agreed, arg=".item-nav", timeout=SLOW)
        top = await w.page.evaluate(
            "parseFloat(getComputedStyle(document.querySelector('.item-nav')).top)"
        )
        bar = await w.page.evaluate("document.getElementById('top').getBoundingClientRect().height")
        assert abs(top - bar) < 0.5
        # There is no panel or drawer that could stick under it as well.
        assert await w.page.locator("#panel, #drawer").count() == 0
        await w.page.set_viewport_size({"width": 700, "height": 1500})
        await w.page.wait_for_function(agreed, arg=".item-nav", timeout=SLOW)
        await w.page.set_viewport_size({"width": 1500, "height": 1500})
        # The Activity page keeps who is working on what in view beside the feed, below the bar.
        await w.go("#/activity")
        await expect(w.page.locator("#working")).to_be_visible(timeout=SLOW)
        await w.page.wait_for_function(agreed, arg=".activity-side", timeout=SLOW)
        assert await w.page.locator("#panel, #drawer").count() == 0
