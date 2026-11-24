"""M40 on the board: a key that verifies other agents' work, and checks only a verifier may tick.

Driven in a real headless browser and compared with what a real MCP client reads (see ./check.sh).
The Users page makes a verifier key (an unchecked box that says what it lets an agent do), shows a
chip on that key's row and its name card and on no other, and changes the box later, which an open
agent feels on its next call; the Definition of done page shows the three tags with their chips
and explains them in its Key; the page of a story or task shows the chip of each check and says,
under a [github] tick that an agent made, that the agent reported it and GitHub did not confirm it,
with the pull request's address as a link only when it is one; and `is:unverified` typed into the
Board's filter finds what `list_nodes` finds. Every test makes an account and a project of its
own, so the shared project's definition of done is never touched."""

import re
from typing import Any

import pytest
from playwright.async_api import Page, Route, ViewportSize, expect

from tests.board_helpers import (
    SLOW,
    apply_filter,
    browser_page,
    cards_are,
    go,
    nothing_open,
    open_item,
)
from tests.helpers import all_nodes, connect, make_tree, ok, refused, unique_word
from tests.test_board_code_mode_integration import key_named
from tests.world import Project, join_project, make_key, make_limited_key, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

TESTS = "Tests pass"
REVIEWED = "Reviewed by an agent that did not write it"
MERGED = "The pull request is merged and its checks passed"
PHONE = "Looks right on a phone"
PULL_REQUEST = "https://github.com/example-org/bike-share/pull/12"
OTHER_PULL_REQUEST = "https://github.com/example-org/bike-share/pull/13"
SEEN = f"{PULL_REQUEST} merged 2026-11-22, merge commit abcdef1234, checks passed 3 of 3"
ONLY_VERIFIER = "may tick the checks marked [verifier] or [github] on work it did not do itself"
BODY = (
    f"Before it is done:\n- {TESTS}\n- [verifier] {REVIEWED}\n- [github] {MERGED}"
    f"\n- [person] {PHONE}\n"
)
NARROW: ViewportSize = {"width": 390, "height": 900}


def define(project: Project, body: str) -> None:
    """The definition of done, written as its owner would (only a person can)."""
    where = f"/board/api/projects/{project.id}/tool"
    current = project.owner.call("POST", f"{where}/get_definition_of_done", {})
    assert current.status == 200, current.body
    saved = project.owner.call(
        "POST",
        f"{where}/set_definition_of_done",
        {"body": body, "expected_version": current.body["version"]},
    )
    assert saved.status == 200, saved.body


async def worked_task(doer: Any) -> str:
    """A task that the agent `doer` claimed and handed over as blocked, ready for a verifier."""
    _, _, task = await make_tree(doer)
    ref = str(task["ref"])
    await ok(doer, "claim_node", node=ref)
    await ok(
        doer,
        "finish_work",
        node=ref,
        outcome="blocked",
        note="Everything is built. Ready for a verifier.",
    )
    return ref


async def open_dialog_for(page: Page, name: str) -> None:
    row = page.locator("#keys li.key", has_text=name)
    await row.locator("button.change-tools").click()
    await expect(page.locator("dialog#change-tools-dialog")).to_be_visible(timeout=SLOW)


async def save_change(page: Page, key_id: int) -> dict[str, Any]:
    """Save the open Change dialog and give what the page sent."""
    async with page.expect_request(
        lambda r: r.method == "PATCH" and r.url.endswith(f"/keys/{key_id}")
    ) as sent:
        await page.locator("dialog#change-tools-dialog button[type=submit]").click()
    body = (await sent.value).post_data_json
    await expect(page.locator("dialog#change-tools-dialog")).to_have_count(0, timeout=SLOW)
    assert isinstance(body, dict)
    return body


async def make_key_in_dialog(
    page: Page, name: str, *, verifier: bool
) -> tuple[str, dict[str, Any]]:
    """Make a key of the New agent key dialog (one tool at a time, the standard tools), with the
    verifier box as asked. Gives the key and what the page sent."""
    await page.locator("#key-open").click()
    await expect(page.locator("dialog#new-key-dialog")).to_be_visible(timeout=SLOW)
    await page.locator("#key-name").fill(name)
    await page.locator("#key-call-tools").check()
    await page.locator("#key-tools-choice-standard").click()
    if verifier:
        await page.locator("#key-verifier").check()
    async with page.expect_request(
        lambda r: r.method == "POST" and r.url.endswith("/keys")
    ) as sent:
        await page.locator("#create-key").click()
    body = (await sent.value).post_data_json
    await expect(page.locator("#key-secret")).to_contain_text("vrk_", timeout=SLOW)
    await expect(page.locator("dialog#new-key-dialog")).to_have_count(0)
    assert isinstance(body, dict)
    return (await page.locator("#key-secret").inner_text()).strip(), body


async def sideways(page: Page) -> dict[str, Any]:
    """How far anything sticks out of the window sideways (the page, or what sits in it), apart
    from what scrolls in a region of its own."""
    found: dict[str, Any] = await page.evaluate(
        """() => {
            const width = window.innerWidth;
            const inside = (e) => e.closest('.table-scroll, .gv-tree, svg, dialog');
            const offenders = [...document.querySelectorAll('body *')]
                .filter((e) => !inside(e) && e.checkVisibility()
                    && e.getBoundingClientRect().width > 0
                    && e.getBoundingClientRect().right > width + 1)
                .map((e) => e.tagName.toLowerCase() + (e.id ? '#' + e.id : '')
                    + '.' + e.className);
            return { width, doc: document.documentElement.scrollWidth,
                     offenders: offenders.slice(0, 8) };
        }"""
    )
    return found


# -- the Users page ------------------------------------------------------------------------------


async def test_the_new_key_dialog_has_an_unchecked_box_and_only_a_verifier_shows_it() -> None:
    word = unique_word()
    owner = register(name=f"Keymaker {word}")
    project = make_project(owner, f"Verifier keys {word}")
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await w.open("#/users")
        await expect(page.locator("#keys li.key")).to_have_count(1, timeout=SLOW)

        # The box comes last, after how it calls Vrum and what it may do, starts unchecked and has
        # ONE quiet line under it. Nothing in the dialog is open.
        await page.locator("#key-open").click()
        dialog = page.locator("dialog#new-key-dialog")
        await expect(dialog).to_be_visible(timeout=SLOW)
        box = dialog.locator("input#key-verifier")
        await expect(box).not_to_be_checked()
        await expect(dialog.locator("label[for=key-verifier]")).to_have_text(
            "Verifies other agents’ work"
        )
        help_line = dialog.locator("#key-verifier-help")
        await expect(help_line).to_have_text(
            "May tick the checks marked [verifier] or [github] on work it did not do itself."
        )
        assert await help_line.evaluate("el => el.tagName") == "P"
        tops = await page.evaluate(
            "ids => ids.map(id => document.getElementById(id).getBoundingClientRect().top)",
            ["key-call-title", "key-tools-title", "key-verifier"],
        )
        assert tops == sorted(tops) and len(set(tops)) == 3, tops
        await nothing_open(page)
        # The box can be reached and changed from the keyboard.
        await box.focus()
        await page.keyboard.press("Space")
        await expect(box).to_be_checked()
        await page.keyboard.press("Space")
        await expect(box).not_to_be_checked()
        await page.keyboard.press("Escape")
        await expect(dialog).to_have_count(0)

        # A key made with the box unchecked says nothing about it: no chip, no "No", false in it.
        _, posted = await make_key_in_dialog(page, f"plain-{word}", verifier=False)
        assert posted["verifier"] is False
        plain_row = page.locator("#keys li.key", has_text=f"plain-{word}")
        await expect(plain_row).to_have_count(1, timeout=SLOW)
        await expect(plain_row.locator(".chip.verifier-only")).to_have_count(0)
        await expect(plain_row).not_to_contain_text("erifier")
        assert key_named(owner, project.id, f"plain-{word}")["verifier"] is False

        # A verifier's key sends true, and its row carries ONE small chip with an icon and the word.
        verifier, posted = await make_key_in_dialog(page, f"checker-{word}", verifier=True)
        assert posted["verifier"] is True
        row = page.locator("#keys li.key", has_text=f"checker-{word}")
        chip = row.locator(".chip.verifier-only")
        await expect(chip).to_have_count(1, timeout=SLOW)
        await expect(chip).to_have_text("Verifier")
        await expect(chip.locator("svg.icon-shield")).to_have_count(1)
        assert key_named(owner, project.id, f"checker-{word}")["verifier"] is True
        await expect(page.locator("#keys .chip.verifier-only")).to_have_count(1)
        await expect(page.locator("#keys li.key")).to_have_count(3)

        # What it means is in a hover card, and the chip can be reached by the keyboard.
        assert await chip.get_attribute("tabindex") == "0"
        await chip.hover()
        card = page.locator(".hovercard:visible")
        await expect(card).to_have_count(1, timeout=SLOW)
        await expect(card).to_contain_text("Verifies other agents’ work")
        await expect(card).to_contain_text(ONLY_VERIFIER)
        await page.keyboard.press("Escape")
        await expect(page.locator(".hovercard:visible")).to_have_count(0)
        await page.mouse.move(5, 5)
        await chip.focus()
        await expect(page.locator(".hovercard:visible")).to_contain_text(ONLY_VERIFIER)
        await page.keyboard.press("Escape")

        # The name card of the agent says "Verifier" for the verifier and nothing for the other.
        await row.locator(".actor.has-card").hover()
        named = page.locator(".hovercard:visible .actor-card")
        await expect(named.locator(".actor-card-head .chip.verifier-only")).to_have_text(
            "Verifier", timeout=SLOW
        )
        await expect(named).to_contain_text("Calls Vrum")
        await page.keyboard.press("Escape")
        await page.mouse.move(5, 5)
        await plain_row.locator(".actor.has-card").hover()
        other = page.locator(".hovercard:visible .actor-card")
        await expect(other).to_contain_text("Calls Vrum", timeout=SLOW)
        await expect(other.locator(".chip.verifier-only")).to_have_count(0)
        await expect(other).not_to_contain_text("erifier")
        await page.keyboard.press("Escape")

        # The key the page showed is a verifier's to the server: its identity carries it.
        async with connect(project.mcp_url, verifier) as agent:
            assert "set_check" in {t.name for t in (await agent.list_tools()).tools}
        assert w.problems == [], w.problems


async def test_the_change_dialog_sets_and_clears_it_and_an_open_agent_feels_it() -> None:
    word = unique_word()
    owner = register(name=f"Changer {word}")
    project = make_project(owner, f"Change verifier {word}")
    define(project, f"- {TESTS}\n- [verifier] {REVIEWED}\n")
    name = f"checker-{word}"
    secret = make_limited_key(project, name, None)
    key_id = key_named(owner, project.id, name)["id"]
    async with (
        connect(project.mcp_url, make_key(project, f"worker-{word}")) as doer,
        connect(project.mcp_url, secret) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        page = w.page
        task = await worked_task(doer)
        wrong = "only a verifier can judge"
        assert wrong in await refused(
            agent, "set_check", node=task, item=REVIEWED, evidence="Read it"
        )

        await w.open("#/users")
        row = page.locator("#keys li.key", has_text=name)
        await expect(row.locator(".key-calls")).to_have_text("One tool at a time", timeout=SLOW)
        await expect(row.locator(".chip.verifier-only")).to_have_count(0)

        # The Change dialog has the same box, unchecked while the key is not a verifier's. With the
        # box alone changed, only the box is sent.
        await open_dialog_for(page, name)
        box = page.locator("dialog#change-tools-dialog input#key-verifier-change")
        await expect(box).not_to_be_checked()
        await expect(page.locator("#key-verifier-change-help")).to_contain_text(
            ONLY_VERIFIER.replace("may", "May", 1)
        )
        await nothing_open(page)
        await box.check()
        assert await save_change(page, key_id) == {"verifier": True}
        await expect(row.locator(".chip.verifier-only")).to_have_text("Verifier", timeout=SLOW)
        assert key_named(owner, project.id, name)["verifier"] is True
        # The agent that has been connected all along is a verifier from its very next call.
        ticked = await ok(agent, "set_check", node=task, item=REVIEWED, evidence="Read the diff")
        assert [c["checked"] for c in ticked["checks"]] == [False, True]

        # Cleared again: the box starts checked, only the box is sent, the chip goes and the agent
        # cannot take its tick back.
        await open_dialog_for(page, name)
        await expect(box).to_be_checked()
        await box.uncheck()
        assert await save_change(page, key_id) == {"verifier": False}
        await expect(row.locator(".chip.verifier-only")).to_have_count(0, timeout=SLOW)
        assert key_named(owner, project.id, name)["verifier"] is False
        said = await refused(
            agent, "set_check", node=task, item=REVIEWED, checked=False, evidence="No"
        )
        assert wrong in said

        # With the tools changed too, both are sent together.
        await open_dialog_for(page, name)
        await page.locator("#key-tools-change-choice-read").click()
        await box.check()
        sent = await save_change(page, key_id)
        assert set(sent) == {"tools", "verifier"} and sent["verifier"] is True
        await expect(row.locator(".chip.verifier-only")).to_have_count(1, timeout=SLOW)
        await expect(row.locator(".key-allowance")).to_have_attribute("data-allowance", "read")
        assert w.problems == [], w.problems


# -- the Definition of done page -----------------------------------------------------------------


async def test_the_definition_page_shows_each_tag_with_its_chip_and_its_key_explains_them() -> None:
    word = unique_word()
    owner = register(name=f"Definer {word}")
    project = make_project(owner, f"Definition tags {word}")
    define(project, f"{BODY}- [person] [github] Signed off by a person\n")
    async with connect(project.mcp_url, project.key) as agent:
        items = (await ok(agent, "get_definition_of_done"))["items"]
    flags = [(i["person_only"], i["verifier_only"], i["github"]) for i in items]
    assert flags == [
        (False, False, False),
        (False, True, False),
        (False, True, True),
        (True, False, False),
        (True, True, True),
    ]
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await go(page, project.address, "#/definition")
        lines = page.locator(".check-ticks li")
        await expect(lines).to_have_count(5, timeout=SLOW)
        await expect(lines.locator(".check-ticks-text")).to_have_text(
            [TESTS, REVIEWED, MERGED, PHONE, "Signed off by a person"]
        )
        # One chip a check: a person outranks the others and [github] already means [verifier], so a
        # [github] line says GitHub alone. The row says what the server says.
        shown = ["", "verifier-only", "github-only", "person-only", "person-only"]
        for at, wanted in enumerate(shown):
            chips = lines.nth(at).locator(".chip")
            await expect(chips).to_have_count(1 if wanted else 0)
            if wanted:
                await expect(chips).to_have_class(re.compile(rf"\b{wanted}\b"))
            row = lines.nth(at)
            assert [
                await row.get_attribute("data-person-only"),
                await row.get_attribute("data-verifier-only"),
                await row.get_attribute("data-github"),
            ] == [str(flag).lower() for flag in flags[at]]
        await expect(lines.nth(1).locator(".chip")).to_have_text("Verifier")
        await expect(lines.nth(2).locator(".chip")).to_have_text("GitHub")
        await expect(lines.nth(3).locator(".chip")).to_have_text("Person only")
        assert await lines.nth(1).locator("svg.icon-shield").count() == 1
        assert await lines.nth(2).locator("svg.icon-branch").count() == 1
        for tag in ("[verifier]", "[github]", "[person]"):
            assert tag not in await page.locator("#definition-field").inner_text()

        # The Key says how each is written, with the same chips, and that Vrum does not ask GitHub.
        await page.locator("#definition-key-button").hover()
        key = page.locator(".hovercard:visible")
        await expect(key).to_have_count(1, timeout=SLOW)
        for tag in ("[person]", "[verifier]", "[github]"):
            await expect(key).to_contain_text(tag)
        await expect(key.locator(".chip.person-only")).to_have_count(1)
        await expect(key.locator(".chip.verifier-only")).to_have_count(1)
        await expect(key.locator(".chip.github-only")).to_have_count(1)
        await expect(key).to_contain_text("Vrum does not ask GitHub")
        await expect(key).to_contain_text("adding or removing one keeps every tick")
        await page.keyboard.press("Escape")
        await expect(page.locator(".hovercard:visible")).to_have_count(0)

        # Tags in any order and any case are not part of a line's words, as the server reads it: the
        # count while typing is the server's, and saving changes no check's key.
        before = {i["text"]: i["item"] for i in items}
        await page.locator("#edit-definition").click()
        body = page.locator("#definition-body")
        await body.fill(
            f"- {TESTS}\n- [VERIFIER] {REVIEWED}\n- [x] [Github] {MERGED}"
            f"\n- [Person] {PHONE}\n- [github] [person] Signed off by a person"
        )
        await expect(page.locator("#definition-count")).to_have_text("5 checks")
        await expect(page.locator("#definition-lost")).to_have_count(0)
        await page.locator("form.definition-form button[type=submit]").click()
        await expect(page.locator("#edit-definition")).to_be_visible(timeout=SLOW)
        async with connect(project.mcp_url, project.key) as agent:
            after = (await ok(agent, "get_definition_of_done"))["items"]
        assert {i["text"]: i["item"] for i in after} == before
        assert w.problems == [], w.problems


# -- the page of a story or task -----------------------------------------------------------------


async def test_an_agents_github_tick_is_reported_with_a_link_and_a_persons_tick_is_not() -> None:
    word = unique_word()
    owner = register(name=f"Reader {word}")
    project = make_project(owner, f"Reported ticks {word}")
    define(project, BODY)
    verifier = f"reviewer-{word}"
    async with (
        connect(project.mcp_url, make_key(project, f"worker-{word}")) as doer,
        connect(project.mcp_url, make_key(project, verifier, verifier=True)) as checker,
    ):
        first = await worked_task(doer)
        second = await worked_task(doer)
        await ok(checker, "set_check", node=first, item=REVIEWED, evidence="Read the diff")
        await ok(checker, "set_check", node=first, item=MERGED, evidence=SEEN)
        async with browser_page(project.address, token=project.key, name=None) as w:
            page = w.page
            await open_item(w, first)
            part = page.locator("#item-page [data-part=definition]")
            rows = part.locator(".check-item")
            await expect(part.locator(".check-text")).to_have_text(
                [TESTS, REVIEWED, MERGED, PHONE], timeout=SLOW
            )

            # One chip a check, as on the Definition of done page: none for an ordinary check,
            # Verifier for [verifier], GitHub alone for [github], Person only for [person]. A tag
            # is not in the words.
            for at, wanted in enumerate(["", "verifier-only", "github-only", "person-only"]):
                chips = rows.nth(at).locator(".chip")
                await expect(chips).to_have_count(1 if wanted else 0)
                if wanted:
                    await expect(chips).to_have_class(re.compile(rf"\b{wanted}\b"))
            await expect(rows.nth(1).locator(".chip")).to_have_text("Verifier")
            await expect(rows.nth(2).locator(".chip")).to_have_text("GitHub")

            # A verifier's tick of an ordinary [verifier] check says nothing more than who and how.
            await expect(rows.nth(1).locator(".check-evidence")).to_have_text("Read the diff")
            await expect(rows.nth(1).locator(".check-reported")).to_have_count(0)
            await expect(rows.nth(1).locator("a")).to_have_count(0)

            # The [github] tick: the address is a link that opens in a new tab and cannot reach the
            # page that opened it, the rest is as written, and ONE quiet line says who reported it
            # and that GitHub did not confirm it (Vrum never asked).
            github = rows.nth(2)
            evidence = github.locator(".check-evidence")
            await expect(evidence).to_have_text(SEEN)
            link = evidence.locator("a.check-pull-request")
            await expect(link).to_have_count(1)
            await expect(link).to_have_text(PULL_REQUEST)
            assert await link.get_attribute("href") == PULL_REQUEST
            assert await link.get_attribute("target") == "_blank"
            assert set((await link.get_attribute("rel") or "").split()) >= {
                "noopener",
                "noreferrer",
            }
            reported = github.locator(".check-reported")
            await expect(reported).to_have_count(1)
            await expect(reported).to_have_text(f"Reported by {verifier}, not confirmed by GitHub.")
            assert "quiet" in (await reported.get_attribute("class") or "")
            assert "verified by GitHub" not in await github.inner_text()
            # The line names the agent as an agent, and can be hovered for its card like any name.
            await expect(reported.locator(".actor-agent")).to_have_count(1)
            assert await rows.nth(3).locator(".check-reported").count() == 0
            assert await rows.nth(0).locator(".check-reported").count() == 0

            # Following the link opens a new tab (answered here, so nothing leaves the machine).
            async def stand_in(route: Route) -> None:
                await route.fulfill(
                    body="a stand-in for the pull request page", content_type="text/plain"
                )

            await page.context.route("https://github.com/**", stand_in)
            async with page.expect_popup() as popup:
                await link.click()
            opened = await popup.value
            assert opened.url == PULL_REQUEST
            await opened.close()
            assert page.url.startswith(project.address)

            # A person's tick of the same sort of check (with an address in their words) shows none
            # of this.
            await open_item(w, second)
            part = page.locator("#item-page [data-part=definition]")
            row = part.locator(".check-item").nth(2)
            await expect(row.locator(".chip.github-only")).to_have_count(1, timeout=SLOW)
            await row.locator("button.check-how").click()
            await row.locator("input.check-how-field").fill(f"{OTHER_PULL_REQUEST} checked by hand")
            await row.locator("form.check-form button[type=submit]").click()
            await expect(row).to_have_attribute("data-checked", "true", timeout=SLOW)
            await expect(row.locator(".check-evidence")).to_have_text(
                f"{OTHER_PULL_REQUEST} checked by hand"
            )
            await expect(row.locator("a")).to_have_count(0)
            await expect(row.locator(".check-reported")).to_have_count(0)
            await expect(row.locator(".check-who")).to_contain_text(f"Reader {word}")
            ticked = (await ok(checker, "get_node", node=second))["checks"][2]
            assert (ticked["checked_by"], ticked["checked_by_kind"]) == (f"Reader {word}", "person")
            assert w.problems == [], w.problems


async def test_only_an_address_that_is_a_pull_request_becomes_a_link_and_nothing_else_does() -> (
    None
):
    word = unique_word()
    owner = register(name=f"Careful {word}")
    project = make_project(owner, f"Plain evidence {word}")
    lines = {
        "Checked over http": "http://github.com/example-org/bike-share/pull/16 merged",
        "Checked by script": "javascript:alert(1) merged",
        "Checked on a files page": "https://github.com/example-org/bike-share/pull/17/files merged",
        "Checked on an issue": "https://github.com/example-org/bike-share/issues/18 merged",
        "Checked with a query": "https://github.com/example-org/bike-share/pull/19?x=1 merged",
        "Checked with a port": "https://github.com:8443/example-org/bike-share/pull/20 merged",
        "Checked for real": SEEN,
    }
    # They are first ticked as [verifier] checks, which take any words. The tag is added afterwards
    # (a tag is not part of a line's words, so every tick stays), which is how a tick comes to be a
    # [github] tick with other words.
    define(project, "".join(f"- [verifier] {words}\n" for words in lines))
    async with (
        connect(project.mcp_url, make_key(project, f"worker-{word}")) as doer,
        connect(project.mcp_url, make_key(project, f"reviewer-{word}", verifier=True)) as checker,
    ):
        task = await worked_task(doer)
        for words, evidence in lines.items():
            await ok(checker, "set_check", node=task, item=words, evidence=evidence)
        define(project, "".join(f"- [github] {words}\n" for words in lines))
        async with browser_page(project.address, token=project.key, name=None) as w:
            page = w.page
            await open_item(w, task)
            rows = page.locator("#item-page [data-part=definition] .check-item")
            await expect(rows).to_have_count(len(lines), timeout=SLOW)
            for at, (words, evidence) in enumerate(lines.items()):
                row = rows.nth(at)
                await expect(row.locator(".check-text")).to_have_text(words)
                await expect(row.locator(".check-evidence")).to_have_text(evidence)
                await expect(row.locator(".check-reported")).to_have_count(1)
                if evidence == SEEN:
                    await expect(row.locator("a")).to_have_count(1)
                else:
                    # Plain text: nothing to follow, and nothing a click could make run.
                    await expect(row.locator("a")).to_have_count(0)
            assert await page.locator("#item-page a[href^='javascript']").count() == 0
            assert w.problems == [], w.problems


# -- the filter ----------------------------------------------------------------------------------


async def test_is_unverified_in_the_filter_finds_what_list_nodes_finds_and_the_help_names_it() -> (
    None
):
    word = unique_word()
    owner = register(name=f"Finder {word}")
    project = make_project(owner, f"Unverified {word}")
    define(project, f"- {TESTS}\n- [verifier] {REVIEWED}\n")
    async with (
        connect(project.mcp_url, make_key(project, f"worker-{word}")) as doer,
        connect(project.mcp_url, make_key(project, f"reviewer-{word}", verifier=True)) as checker,
    ):
        waiting = [await worked_task(doer) for _ in range(2)]
        verified = await worked_task(doer)
        await ok(checker, "set_check", node=verified, item=REVIEWED, evidence="Read the diff")
        _, _, loose = await make_tree(doer)
        found = {n["ref"] for n in await all_nodes(doer, query="is:unverified")}
        assert found == set(waiting)
        assert verified not in found and loose["ref"] not in found
        # It combines with the other terms, as the board's manual says (one epic's waiting work).
        story = (await ok(doer, "get_node", node=waiting[0]))["parent"]
        epic = (await ok(doer, "get_node", node=story))["parent"]
        one = await all_nodes(doer, query=f"is:unverified {epic}")
        assert {n["ref"] for n in one} == {waiting[0]}
        async with browser_page(project.address, token=project.key, name=None) as w:
            page = w.page
            await go(page, project.address, "#/board")
            await expect(page.locator(".card")).to_have_count(4, timeout=SLOW)
            await apply_filter(page, "is:unverified")
            await cards_are(page, found)
            await expect(page.locator("#filter-problem")).to_have_text("")
            # The same filter asked for in the address finds the same on the Lists page.
            await go(page, project.address, "#/lists?view=work&q=is%3Aunverified")
            rows = page.locator("#work-tree li[role=treeitem]")
            await expect(rows.first).to_be_visible(timeout=SLOW)
            keys = await rows.evaluate_all("els => els.map(e => e.dataset.key)")
            assert {key for key in keys if key.startswith("task:")} == found
            await expect(page.locator("#filter")).to_have_value("is:unverified")

            # The help says it, with an example.
            await page.locator("#filter-info").hover()
            help_card = page.locator(".hovercard:visible")
            await expect(help_card).to_have_count(1, timeout=SLOW)
            line = help_card.locator("dt", has_text=re.compile(r"^is:"))
            await expect(line).to_contain_text("unverified")
            await expect(line).to_contain_text("waiting for a verifier")
            await expect(help_card.locator("dd code", has_text="is:unverified")).to_have_count(1)
            await page.keyboard.press("Escape")
            assert w.problems == [], w.problems


# -- a narrow window and the keyboard --------------------------------------------------------------


async def test_the_new_pages_fit_390_pixels_and_every_control_is_reached_from_the_keyboard() -> (
    None
):
    word = unique_word()
    owner = register(name=f"Narrow {word}")
    project = make_project(owner, f"Narrow verifier {word}")
    define(project, BODY)
    reviewer = make_key(project, f"reviewer-{word}", verifier=True)
    async with (
        connect(project.mcp_url, make_key(project, f"worker-{word}")) as doer,
        connect(project.mcp_url, reviewer) as checker,
    ):
        task = await worked_task(doer)
        await ok(checker, "set_check", node=task, item=MERGED, evidence=SEEN)
        async with browser_page(project.address, token=project.key, name=None) as w:
            page = w.page
            await page.set_viewport_size(NARROW)

            await go(page, project.address, "#/users")
            await expect(page.locator("#keys .chip.verifier-only")).to_have_count(1, timeout=SLOW)
            found = await sideways(page)
            assert found["doc"] <= found["width"] and found["offenders"] == [], found
            await page.locator("#key-open").click()
            await expect(page.locator("dialog#new-key-dialog")).to_be_visible(timeout=SLOW)
            box = page.locator("#key-verifier")
            await box.scroll_into_view_if_needed()
            await box.focus()
            await page.keyboard.press("Space")
            await expect(box).to_be_checked()
            fits = await page.evaluate(
                """() => {
                    const d = document.querySelector('dialog#new-key-dialog')
                        .getBoundingClientRect();
                    const l = document.getElementById('key-verifier-help')
                        .getBoundingClientRect();
                    return [d.left >= 0, d.right <= window.innerWidth, l.right <= d.right];
                }"""
            )
            assert fits == [True, True, True], fits
            await page.keyboard.press("Escape")
            await expect(page.locator("dialog#new-key-dialog")).to_have_count(0)

            await go(page, project.address, "#/definition")
            await expect(page.locator(".check-ticks li")).to_have_count(4, timeout=SLOW)
            await page.locator("#definition-key-button").click()
            await expect(page.locator(".hovercard:visible")).to_have_count(1, timeout=SLOW)
            inside = await page.evaluate(
                """() => {
                    const r = document.querySelector('.hovercard:not([hidden])')
                        .getBoundingClientRect();
                    return r.left >= 0 && r.right <= window.innerWidth;
                }"""
            )
            assert inside
            await page.keyboard.press("Escape")
            found = await sideways(page)
            assert found["doc"] <= found["width"] and found["offenders"] == [], found

            await open_item(w, task)
            await expect(page.locator(".check-reported")).to_have_count(1, timeout=SLOW)
            found = await sideways(page)
            assert found["doc"] <= found["width"] and found["offenders"] == [], found
            # The boxes are real controls: a keyboard ticks the person's check, as a click does.
            phone = page.locator("#item-page .check-item").nth(3).locator("input.check-box")
            await phone.focus()
            await page.keyboard.press("Space")
            await expect(page.locator("#item-page .check-item").nth(3)).to_have_attribute(
                "data-checked", "true", timeout=SLOW
            )
            assert w.problems == [], w.problems


async def test_someone_who_can_only_look_sees_the_chips_and_the_reported_line() -> None:
    word = unique_word()
    owner = register(name=f"Showing {word}")
    project = make_project(owner, f"Looking {word}")
    define(project, BODY)
    async with (
        connect(project.mcp_url, make_key(project, f"worker-{word}")) as doer,
        connect(project.mcp_url, make_key(project, f"reviewer-{word}", verifier=True)) as checker,
    ):
        task = await worked_task(doer)
        await ok(checker, "set_check", node=task, item=MERGED, evidence=SEEN)
        viewer = join_project(project, "viewer", f"Viewer {word}")
        async with browser_page(project.address, token=None, person=viewer) as v:
            await open_item(v, task)
            rows = v.page.locator("#item-page [data-part=definition] .check-item")
            await expect(rows.nth(2).locator(".chip.github-only")).to_have_count(1, timeout=SLOW)
            await expect(rows.nth(2).locator(".check-reported")).to_have_count(1)
            await expect(rows.nth(2).locator("a.check-pull-request")).to_have_attribute(
                "href", PULL_REQUEST
            )
            assert await rows.locator("input.check-box:disabled").count() == 4
            await v.open("#/users")
            await expect(v.page.locator("#keys-owner-only")).to_be_visible(timeout=SLOW)
            await expect(v.page.locator(".chip.verifier-only")).to_have_count(0)
            assert v.problems == [], v.problems
