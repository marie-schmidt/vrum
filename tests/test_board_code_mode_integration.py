"""M37's code mode on the board (the seventh proof): the Users page makes an agent key of either
kind and refuses a missing choice in words, says which way a key calls Vrum and changes it with
what the key may do, an open agent sees the change on its next call, and only the owner does any of
it; the Tools page shows the two tools of a key that runs code with their own chip and switch, runs
`explore` in Try it and turns `run` away. Everything the page shows is compared with what a real MCP
client with the key lists and gets. (How the dialogs and the rows look in both themes and at 390
pixels is audited by the tour of every screen in board_layout.py.)"""

import re
from typing import Any

import pytest
from playwright.async_api import Page, expect

from tests.board_helpers import SLOW, api_path, browser_page, nothing_open
from tests.helpers import connect, ok, refused, unique_word
from tests.test_board_tools_integration import listed, try_it
from tests.world import Person, join_project, make_limited_key, make_project, register
from vrum.surface import AGENT_TOOLS

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

CODE_TOOLS = {"explore", "run"}


def keys_of(owner: Person, project_id: int) -> list[dict[str, Any]]:
    """The project's keys as the board's own API lists them, in force and revoked."""
    reply = owner.call("GET", f"/board/api/projects/{project_id}/keys")
    assert reply.status == 200, reply.body
    return list(reply.body["keys"])


def key_named(owner: Person, project_id: int, name: str) -> dict[str, Any]:
    (found,) = [key for key in keys_of(owner, project_id) if key["name"] == name]
    return found


async def tools_listed(agent: Any) -> set[str]:
    return {tool.name for tool in (await agent.list_tools()).tools}


async def open_new_key_dialog(page: Page) -> None:
    await page.locator("#key-open").click()
    await expect(page.locator("dialog#new-key-dialog")).to_be_visible(timeout=SLOW)
    await expect(page.locator("#key-name")).to_be_focused()


async def create_key(page: Page, name: str, way: str, allowed: str) -> str:
    """Make a key in the open New agent key dialog, choosing how it calls Vrum (`code` or `tools`)
    and what it may do, and give the key that is shown once."""
    await page.locator("#key-name").fill(name)
    await page.locator(f"#key-call-{way}").check()
    await page.locator(f"#key-tools-choice-{allowed}").click()
    await page.locator("#create-key").click()
    await expect(page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
    await expect(page.locator("dialog#new-key-dialog")).to_have_count(0)
    return (await page.locator("#key-secret").inner_text()).strip()


# -- the Users page ------------------------------------------------------------------------------


async def test_the_new_key_dialog_asks_how_it_calls_vrum_and_makes_a_key_of_either_kind() -> None:
    word = unique_word()
    owner = register(name=f"Scripter {word}")
    project = make_project(owner, f"Code keys {word}")
    tools = owner.call("GET", "/board/api/tools").body["tools"]
    readers = {t["name"] for t in tools if t["offered_to_agents"] and not t["changes_something"]}
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await w.open("#/users")
        await expect(page.locator("#keys li.key")).to_have_count(1, timeout=SLOW)
        await open_new_key_dialog(page)
        dialog = page.locator("dialog#new-key-dialog")
        # How it calls Vrum is a decision: both ways are offered, each with a line on what it
        # means, neither is chosen, and nothing in the dialog is open.
        await expect(page.locator("#key-call-title")).to_have_text("How it calls Vrum")
        ways = dialog.locator("#key-call label.call-choice")
        assert await ways.locator("strong").all_inner_texts() == [
            "By running code",
            "One tool at a time",
        ]
        assert await ways.evaluate_all("els => els.map(e => e.querySelector('input').value)") == [
            "code",
            "tools",
        ]
        await expect(ways.first.locator(".quiet")).to_contain_text("explore")
        await expect(ways.first.locator(".quiet")).to_contain_text("run")
        await expect(dialog.locator("input[name=key-call]:checked")).to_have_count(0)
        await nothing_open(page)

        # Sending with no way chosen says so in the dialog, which keeps what was typed, and sends
        # nothing: no key appears.
        await page.locator("#key-name").fill(f"coder-{word}")
        await page.locator("#create-key").click()
        await expect(page.locator("#key-form .problem")).to_have_text(
            "Choose how it calls Vrum.", timeout=SLOW
        )
        await expect(dialog).to_be_visible()
        await expect(page.locator("#key-name")).to_have_value(f"coder-{word}")
        await expect(page.locator("#keys li.key")).to_have_count(1)
        assert len(keys_of(owner, project.id)) == 1

        # By running code, reading only: the key lists exactly the two tools, the row says "Runs
        # code", and what it may do is the same choice as for any key (its functions are the ones
        # that only read).
        await page.locator("#key-call-code").check()
        await page.locator("#key-tools-choice-read").click()
        await page.locator("#create-key").click()
        await expect(page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
        await expect(dialog).to_have_count(0)
        coder = (await page.locator("#key-secret").inner_text()).strip()
        await expect(page.locator("#new-key-calls")).to_have_text("How it calls Vrum: Runs code.")
        row = page.locator("#keys li.key", has_text=f"coder-{word}")
        await expect(row.locator(".key-calls")).to_have_text("Runs code")
        await expect(row.locator(".key-calls")).to_have_attribute("data-calls", "code")
        await expect(row.locator(".key-allowance")).to_have_attribute("data-allowance", "read")
        made = key_named(owner, project.id, f"coder-{word}")
        assert made["code_mode"] is True and made["tools"] is not None
        async with connect(project.mcp_url, coder) as agent:
            listing = {t.name: t for t in (await agent.list_tools()).tools}
            assert set(listing) == CODE_TOOLS
            assert listing["explore"].annotations
            assert listing["explore"].annotations.read_only_hint is True
            assert not (listing["run"].annotations and listing["run"].annotations.read_only_hint)
            assert {f["name"] for f in (await ok(agent, "explore"))["functions"]} == readers

        # One tool at a time, with the standard tools: the row says so, and the key lists the
        # tools Vrum offers agents, as a key always did.
        await open_new_key_dialog(page)
        await create_key(page, f"stepper-{word}", "tools", "standard")
        await expect(page.locator("#new-key-calls")).to_have_text(
            "How it calls Vrum: One tool at a time."
        )
        row = page.locator("#keys li.key", has_text=f"stepper-{word}")
        await expect(row.locator(".key-calls")).to_have_text("One tool at a time")
        await expect(row.locator(".key-calls")).to_have_attribute("data-calls", "tools")
        assert key_named(owner, project.id, f"stepper-{word}")["code_mode"] is False
        await expect(page.locator("#keys li.key")).to_have_count(3)
        assert w.problems == []

    # A revoked key still says how it called Vrum, in the fold of revoked keys.
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await w.open("#/users")
        row = page.locator("#keys li.key", has_text=f"coder-{word}")
        await row.locator("button.revoke").click()
        await page.locator("dialog.modal button.primary").click()
        await expect(page.locator("#revoked li.key[data-state=revoked]")).to_have_count(
            1, timeout=SLOW
        )
        await page.locator("details#revoked-keys summary").click()
        gone = page.locator("#revoked li.key", has_text=f"coder-{word}")
        await expect(gone.locator(".key-calls")).to_have_text("Runs code")
        await expect(gone.locator(".key-when")).to_contain_text("Revoked ")
        assert w.problems == []


async def test_change_what_it_may_do_starts_from_the_way_and_an_open_agent_sees_what_changed() -> (
    None
):
    """The dialog starts from the key's way of calling Vrum and sends only what changed; an agent
    connected all the while sees each change on its next call."""
    word = unique_word()
    owner = register(name=f"Changer {word}")
    project = make_project(owner, f"Changed keys {word}")
    tools = owner.call("GET", "/board/api/tools").body["tools"]
    readers = {t["name"] for t in tools if t["offered_to_agents"] and not t["changes_something"]}
    name = f"switcher-{word}"
    secret = make_limited_key(project, name, None, code_mode=True)
    key_id = key_named(owner, project.id, name)["id"]
    keys_path = f"/board/api/projects/{project.id}/keys/{key_id}"

    async with (
        connect(project.mcp_url, secret) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        page = w.page
        assert await tools_listed(agent) == CODE_TOOLS
        await w.open("#/users")
        row = page.locator("#keys li.key", has_text=name)
        await expect(row.locator(".key-calls")).to_have_text("Runs code", timeout=SLOW)

        async def open_change_dialog() -> None:
            await row.locator("button.change-tools").click()
            await expect(page.locator("dialog#change-tools-dialog")).to_be_visible(timeout=SLOW)

        async def save_and_read_what_was_sent() -> dict[str, Any]:
            async with page.expect_request(
                lambda r: r.method == "PATCH" and r.url.endswith(f"/keys/{key_id}")
            ) as sent:
                await page.locator("dialog#change-tools-dialog button[type=submit]").click()
            request = await sent.value
            await expect(page.locator("dialog#change-tools-dialog")).to_have_count(0)
            await expect(page.locator("#notices .notice-text").first).to_have_text(
                "Saved. It applies from the agent’s next request.", timeout=SLOW
            )
            body = request.post_data_json
            assert isinstance(body, dict)
            return body

        # The dialog shows the key's current way, and nothing else is open.
        await open_change_dialog()
        await expect(page.locator("#key-call-change-code")).to_be_checked()
        await expect(page.locator("#key-call-change-tools")).not_to_be_checked()
        await nothing_open(page)
        # Only the way is changed: only the way is sent. The agent that is connected now sees it
        # on its next call: it lists the tools, and what it did before is refused in words.
        await page.locator("#key-call-change-tools").check()
        assert await save_and_read_what_was_sent() == {"code_mode": False}
        await expect(row.locator(".key-calls")).to_have_text("One tool at a time", timeout=SLOW)
        assert await tools_listed(agent) == set(AGENT_TOOLS)
        said = await refused(agent, "explore")
        assert "is for agent keys that call Vrum by running code" in said
        assert f"({name})" in said and "calls one tool at a time" in said
        changed = owner.call("GET", keys_path.rsplit("/", 1)[0]).body["keys"]
        assert [(k["code_mode"], k["tools"]) for k in changed if k["id"] == key_id] == [
            (False, None)
        ]

        # Only the tools are changed: only the tools are sent, and the way stays.
        await open_change_dialog()
        await expect(page.locator("#key-call-change-tools")).to_be_checked()
        await page.locator("#key-tools-change-choice-read").click()
        sent = await save_and_read_what_was_sent()
        assert set(sent) == {"tools"} and set(sent["tools"]) == readers
        await expect(row.locator(".key-allowance")).to_have_attribute(
            "data-allowance", "read", timeout=SLOW
        )
        await expect(row.locator(".key-calls")).to_have_text("One tool at a time")
        assert await tools_listed(agent) == readers

        # Both: back to running code and to the standard tools, in one save.
        await open_change_dialog()
        await page.locator("#key-call-change-code").check()
        await page.locator("#key-tools-change-choice-standard").click()
        assert await save_and_read_what_was_sent() == {"code_mode": True, "tools": None}
        await expect(row.locator(".key-calls")).to_have_text("Runs code", timeout=SLOW)
        await expect(row.locator(".key-allowance")).to_have_count(0)
        assert await tools_listed(agent) == CODE_TOOLS
        said = await refused(agent, "list_nodes")
        assert "calls Vrum by running code" in said and "vrum.list_nodes(...)" in said
        assert {f["name"] for f in (await ok(agent, "explore"))["functions"]} == set(AGENT_TOOLS)
        assert w.problems == []


async def test_only_the_owner_makes_or_changes_keys_and_an_editor_sees_no_button_for_it() -> None:
    word = unique_word()
    owner = register(name=f"Gatekeeper {word}")
    project = make_project(owner, f"Owner only {word}")
    editor = join_project(project, "editor", f"Ed {word}")
    viewer = join_project(project, "viewer", f"Vi {word}")
    before = keys_of(owner, project.id)
    where = f"/board/api/projects/{project.id}/keys"
    for person in (editor, viewer):
        made = person.call("POST", where, {"name": f"sneaky-{word}", "code_mode": True})
        assert made.status == 403, person.name
        assert made.body["message"].startswith("Only the owner of this project can ")
        changed = person.call("PATCH", f"{where}/{before[0]['id']}", {"code_mode": True})
        assert changed.status == 403, person.name
        assert changed.body["message"].startswith("Only the owner of this project can ")
    assert keys_of(owner, project.id) == before
    assert before[0]["code_mode"] is False
    async with browser_page(project.address, token=None, person=editor, name=None) as w:
        await w.open("#/users")
        await expect(w.page.locator("#keys-owner-only")).to_be_visible(timeout=SLOW)
        for gone in ("#key-open", "button.change-tools", "#keys"):
            assert await w.page.locator(gone).count() == 0, gone
        assert w.problems == []


# -- the Tools page ------------------------------------------------------------------------------


async def test_the_tools_page_names_the_code_tools_and_try_it_runs_explore_but_not_run() -> None:
    word = unique_word()
    owner = register(name=f"Reader {word}")
    project = make_project(owner, f"Tools for code {word}")
    board = project.address
    listing = owner.call("GET", "/board/api/tools").body
    tools = listing["tools"]
    offered = {t["name"] for t in tools if t["offered_to_agents"]}
    code = {t["name"] for t in tools if t["code_mode"]}
    assert code == CODE_TOOLS and offered == set(AGENT_TOOLS)
    board_only = {t["name"] for t in tools} - offered - code
    reader = make_limited_key(project, f"reader-{word}", None, code_mode=True)

    async with browser_page(board, token=project.key, name=None) as w:
        page = w.page
        await w.open("#/tools")
        await listed(page)

        async def names() -> list[str]:
            found: list[str] = await page.locator("#tool-list article.tool").evaluate_all(
                "els => els.map(e => e.dataset.name)"
            )
            return found

        # Each of the two wears the chip "For keys that run code", and says who is given it.
        for name in sorted(CODE_TOOLS):
            entry = page.locator(f"#tool-entry-{name}")
            chip = entry.locator(".tool-offer")
            await expect(chip).to_have_text("For keys that run code")
            await expect(chip).to_have_class(re.compile(r"\bfor-code\b"))
            await expect(entry).to_have_attribute("data-offered", "0")
            await expect(entry).to_have_attribute("data-code", "1")
            await expect(entry.locator(".offer-note")).to_have_text(
                "Only a key that runs code is given this tool. "
                "How a key calls Vrum is chosen on the Users page."
            )
        await expect(page.locator("#tool-entry-explore .chip.reads")).to_have_text("Only reads")
        await expect(page.locator("#tool-entry-run .chip.changes")).to_have_text(
            "Changes something"
        )
        # The counts, from the server's own list: all of them; the two that were there before are as
        # they were; the new switch counts the two, which are in neither of the others.
        counts = (
            ("all", len(tools)),
            ("offered", 25),
            ("board", len(board_only)),
            ("code", 2),
        )
        for choice, count in counts:
            await expect(page.locator(f"#tools-show-{choice} .tools-show-count")).to_have_text(
                str(count)
            )
        assert await page.locator("#tools-show-code").inner_text() == "For keys that run code\n2"
        assert len(await names()) == len(tools)

        await page.locator("#tools-show-code").click()
        await expect(page.locator("#tools-show-code")).to_have_attribute("aria-pressed", "true")
        await expect(page.locator("#tool-list article.tool")).to_have_count(2, timeout=SLOW)
        assert sorted(await names()) == sorted(CODE_TOOLS)
        assert page.url.endswith("#/tools?show=code")
        # The address opens the same list, and each of the other two switches leaves them out.
        await w.open("#/tools?show=code")
        await listed(page)
        assert sorted(await names()) == sorted(CODE_TOOLS)
        for choice, wanted in (("offered", offered), ("board", board_only)):
            await page.locator(f"#tools-show-{choice}").click()
            await expect(page.locator("#tool-list article.tool")).to_have_count(
                len(wanted), timeout=SLOW
            )
            assert set(await names()) == wanted, choice
        # The search finds them by who they are for.
        await page.locator("#tools-show-all").click()
        await page.locator("#tool-search").fill("run code")
        await page.locator("#tool-search").press("Enter")
        await expect(page.locator("#tool-list article.tool")).to_have_count(2, timeout=SLOW)
        assert sorted(await names()) == sorted(CODE_TOOLS)
        await page.locator("#clear-tool-search").click()
        await expect(page.locator("#tool-list article.tool")).to_have_count(
            len(tools), timeout=SLOW
        )

        # Try it: `explore` has one and shows what the same call gives a key that runs code (the
        # board has no key, so it lists the standard set, which is what a new key has); `run`
        # changes something, so it has none.
        async with connect(project.mcp_url, reader) as agent:
            shown = await try_it(page, "explore")
            assert shown == await ok(agent, "explore")
            assert {f["name"] for f in shown["functions"]} == set(AGENT_TOOLS)
            shown = await try_it(page, "explore", names="get_node, add_note")
            assert shown == await ok(agent, "explore", names=["get_node", "add_note"])
            assert "get_node" in shown["declarations"] and "add_note" in shown["declarations"]
            # (The form keeps what was typed before, so the names are cleared.)
            shown = await try_it(page, "explore", names="", query="similar")
            assert shown == await ok(agent, "explore", query="similar")
        assert await page.locator("#tool-entry-run details.try").count() == 0
        assert await page.locator("#tool-entry-explore details.try").count() == 1
        assert w.problems == []

    # A try of `run` that is made anyway is turned away, and so is calling it as a board tool.
    asked = {"code": "return 1 + 1"}
    refusal = owner.call("POST", api_path(board, "/try/run"), asked)
    assert refusal.status == 403 and "changes something" in refusal.body["message"]
    assert owner.call("POST", api_path(board, "/tool/run"), asked).status == 404
    assert owner.call("POST", api_path(board, "/try/explore"), {}).status == 200
