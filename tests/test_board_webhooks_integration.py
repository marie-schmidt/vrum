"""M48 on the board: the Webhooks of the Users page and the Email nudges of the Account page.

Driven in a real headless browser, with the check stack's stand-in receiver and stand-in mail
server and a real MCP client for the news (see ./check.sh). Only a project's owner has webhooks: the
section lists exactly what the server says (the kinds, "Every kind of news", on or off, one chip for
a state that is not fine), adds one from a dialog that opens empty and shows the signing secret once
and never again, sends a test and says how it went in the server's words, changes the kinds, turns
it off and on, replaces the secret (the old one signs too for a while), lists the messages and
deletes it; an editor and a viewer have no such section and nothing is asked for them. Any member
may ask for email nudges on the Account page: the section is drawn from what the server says (email
on or off, the address confirmed or not), saves one project at a time and shows a refusal in the
server's words. Every test makes an account and a project of its own."""

import json
import re
from typing import Any

import pytest
from playwright.async_api import Page, ViewportSize, expect

from tests.board_helpers import SLOW, browser_page, nothing_open, on_page, plain
from tests.helpers import connect, unique_word
from tests.test_board_needs_integration import no_overflow
from tests.test_email_integration import sent_to, wait_for_mail
from tests.test_webhooks_integration import (
    EVERYTHING,
    _joined,
    a_person_with_a_confirmed_address,
    add,
    an_ask,
    check,
    deliveries,
    hook,
    path,
    received_at,
    settle,
    wait_for,
    webhook_of,
)
from tests.world import Person, Project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NARROW: ViewportSize = {"width": 390, "height": 900}
SECRET = re.compile(r"^whsec_\S{44}$")


async def an_owner() -> tuple[Person, Project]:
    owner = register()
    return owner, make_project(owner)


def hooks(page: Page) -> Any:
    return page.locator("#webhooks li.webhook")


def row(page: Page, number: int) -> Any:
    return page.locator(f"#webhooks li.webhook[data-webhook='{number}']")


async def open_users(w: Any) -> None:
    await w.open("#/users")
    await on_page(w.page, "users")
    await expect(w.page.locator("#users-webhooks")).to_be_visible(timeout=SLOW)


async def the_secret_shown(page: Page) -> str:
    """The secret in the dialog that shows it, and the dialog's words about it."""
    dialog = page.locator("dialog#webhook-secret-dialog[open]")
    await expect(dialog).to_be_visible(timeout=SLOW)
    await expect(dialog.locator(".webhook-secret-warning")).to_have_text(
        "This is the only time the secret is shown. Put it in the place that receives the messages."
    )
    secret = (await dialog.locator("#webhook-secret").text_content()) or ""
    assert SECRET.match(secret), secret
    return secret


async def close_secret(page: Page) -> None:
    await page.locator("#webhook-secret-close").click()
    await expect(page.locator("dialog#webhook-secret-dialog")).to_have_count(0)


# -- the section ---------------------------------------------------------------------------------


async def test_an_empty_section_says_what_a_webhook_is_and_the_list_is_what_the_server_says() -> (
    None
):
    owner, project = await an_owner()
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_users(w)
        # One sentence and the button that adds one.
        await expect(page.locator("#webhooks-empty")).to_contain_text(
            "A webhook is a signed message that Vrum sends to an address", timeout=SLOW
        )
        await expect(page.locator("#webhooks-empty")).to_contain_text("only a nudge")
        await expect(page.locator("#webhook-open")).to_have_text("Add a webhook")
        await expect(hooks(page)).to_have_count(0)

        few = add(owner, project, hook("ok", unique_word()), ["ask.raised", "work.done"])
        every = add(owner, project, hook("ok", unique_word()), EVERYTHING)
        quiet = add(owner, project, hook("ok", unique_word()), ["claim.lapsed"])
        off = owner.call("PATCH", path(project, f"/{quiet['webhook']['id']}"), {"enabled": False})
        assert off.status == 200, off.body
        await w.go("#/board")
        await w.go("#/users")
        await expect(hooks(page)).to_have_count(3, timeout=SLOW)
        served = owner.call("GET", path(project)).body
        assert [one["id"] for one in served["webhooks"]] == [
            one["webhook"]["id"] for one in (few, every, quiet)
        ]
        ids = await hooks(page).evaluate_all("els => els.map(e => Number(e.dataset.webhook))")
        assert ids == [one["id"] for one in served["webhooks"]]
        for info in served["webhooks"]:
            one = row(page, info["id"])
            # The address as the owner wrote it, as text and never as a link.
            await expect(one.locator(".webhook-address")).to_have_text(info["url"])
            assert await one.locator("a").count() == 0
        # What it is sent for: a chip for each kind, with the server's sentence in its tooltip, or
        # one chip for all of them.
        chips = await row(page, few["webhook"]["id"]).locator(".webhook-kind").all_inner_texts()
        assert chips == ["ask.raised", "work.done"]
        first = row(page, few["webhook"]["id"]).locator(".webhook-kind").first
        assert await first.get_attribute("title") == served["kinds"]["ask.raised"]
        assert await row(page, every["webhook"]["id"]).locator(
            ".webhook-kind"
        ).all_inner_texts() == ["Every kind of news"]
        # A webhook that is fine says nothing about its state; one turned off says it once.
        for one in (few, every):
            await expect(row(page, one["webhook"]["id"]).locator(".webhook-state")).to_have_count(0)
        gone = row(page, quiet["webhook"]["id"])
        await expect(gone.locator(".webhook-state")).to_have_count(1)
        await expect(gone.locator(".webhook-state")).to_have_text("Turned off")
        await expect(gone.locator("button.webhook-switch")).to_have_text("Turn on")
        await expect(row(page, few["webhook"]["id"]).locator("button.webhook-switch")).to_have_text(
            "Turn off"
        )
        buttons = (
            await row(page, few["webhook"]["id"]).locator(".webhook-side button").all_inner_texts()
        )
        assert buttons == ["Test", "Change kinds…", "Turn off", "Replace secret…", "Delete…"]
        # No secret is on the page, the section's data or the list the server gives.
        for made in (few, every, quiet):
            assert made["secret"] not in await page.content()
            assert made["secret"] not in json.dumps(served)
        await no_overflow(page)
        assert w.problems == []


# -- adding a webhook and the secret -------------------------------------------------------------


async def test_adding_one_shows_the_secret_once_and_a_real_message_is_signed_with_it() -> None:
    owner, project = await an_owner()
    tag = unique_word()
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        page = w.page
        await page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        await open_users(w)
        await page.locator("#webhook-open").click()
        dialog = page.locator("dialog#add-webhook-dialog[open]")
        await expect(dialog).to_be_visible()
        await nothing_open(page)
        # It opens empty: no address and no kind chosen, each kind with the server's own sentence.
        await expect(dialog.locator("#webhook-address")).to_have_value("")
        kinds = owner.call("GET", path(project)).body["kinds"]
        assert list(kinds) == EVERYTHING
        boxes = dialog.locator("input[name=kind]")
        await expect(boxes).to_have_count(len(EVERYTHING))
        assert await boxes.evaluate_all("els => els.map(e => e.value)") == EVERYTHING
        assert await dialog.locator("input[name=kind]:checked").count() == 0
        for kind, words in kinds.items():
            await expect(dialog.locator(f"label[for='webhook-add-kind-{kind}']")).to_contain_text(
                words
            )

        # Nothing chosen: the server says so, in the dialog, and nothing is made.
        await dialog.locator("button[type=submit]").click()
        problem = dialog.locator(".problem")
        await expect(problem).to_contain_text("events is a list", timeout=SLOW)
        said = owner.call("POST", path(project), {"url": "", "events": []})
        plain((await problem.text_content()) or "", said.body["message"])
        # An address that may not be reached, with a kind chosen.
        await dialog.locator("#webhook-address").fill("https://127.0.0.1/hook")
        await dialog.locator("input[value='ask.raised']").check()
        await dialog.locator("button[type=submit]").click()
        await expect(problem).to_contain_text("loopback", timeout=SLOW)
        await expect(problem).to_contain_text("Nothing was saved.")
        loopback = owner.call(
            "POST", path(project), {"url": "https://127.0.0.1/hook", "events": ["ask.raised"]}
        )
        plain((await problem.text_content()) or "", loopback.body["message"])
        # A kind the server does not know (the page only offers its own, so the box is tampered
        # with): refused in words too.
        await dialog.locator("input[value='ask.raised']").evaluate("e => { e.value = 'nonsense'; }")
        await dialog.locator("#webhook-address").fill(hook("ok", tag))
        await dialog.locator("button[type=submit]").click()
        await expect(problem).to_contain_text("'nonsense' is not a kind of news", timeout=SLOW)
        assert owner.call("GET", path(project)).body["webhooks"] == []

        # Chosen properly, it is made, and the secret comes once in a dialog of its own.
        await dialog.locator("input[value='nonsense']").evaluate("e => { e.value = 'ask.raised'; }")
        await dialog.locator("input[value='work.blocked']").check()
        await dialog.locator("button[type=submit]").click()
        await expect(page.locator("dialog#add-webhook-dialog")).to_have_count(0, timeout=SLOW)
        secret = await the_secret_shown(page)
        (made,) = owner.call("GET", path(project)).body["webhooks"]
        assert made["events"] == ["ask.raised", "work.blocked"] and made["url"] == hook("ok", tag)
        # Copy puts it on the clipboard.
        await page.locator("dialog#webhook-secret-dialog button.copy-button").click()
        await expect(page.locator("dialog#webhook-secret-dialog button.copy-button")).to_have_text(
            "Copied"
        )
        assert await page.evaluate("navigator.clipboard.readText()") == secret
        # Closed, nothing of it is left: not in the page, the address, the storage or the list.
        await close_secret(page)
        assert secret not in await page.content()
        assert secret not in (await page.evaluate("location.href"))
        stored = await page.evaluate(
            "JSON.stringify([localStorage, sessionStorage, document.cookie])"
        )
        assert secret not in stored
        await expect(row(page, made["id"]).locator(".webhook-address")).to_have_text(made["url"])
        await w.go("#/board")
        await w.go("#/users")
        await expect(hooks(page)).to_have_count(1, timeout=SLOW)
        assert secret not in await page.content()
        assert secret not in json.dumps(owner.call("GET", path(project)).body)

        # A real message arrives for each kind chosen once an agent asks (a question, and the work
        # blocked while it waits), and the secret the page showed checks each.
        await an_ask(agent)
        messages = wait_for(
            lambda: len(received_at(tag)) >= 2 and received_at(tag), "the two messages"
        )
        assert {json.loads(one["body"])["type"] for one in messages} == {
            "ask.raised",
            "work.blocked",
        }
        assert all(check(secret, one) is None for one in messages)
        assert w.problems == []


# -- test, failing and waiting -------------------------------------------------------------------


async def test_test_says_how_it_went_and_a_receiver_that_fails_shows_failing_with_the_words() -> (
    None
):
    owner, project = await an_owner()
    good, bad = unique_word(), unique_word()
    fine = add(owner, project, hook("ok", good), ["ask.raised"])
    broken = add(owner, project, hook("fail", bad), ["ask.raised"])
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_users(w)
        ok_row, bad_row = row(page, fine["webhook"]["id"]), row(page, broken["webhook"]["id"])
        await expect(ok_row).to_be_visible(timeout=SLOW)
        await ok_row.locator("button.webhook-test-button").click()
        sent = ok_row.locator(".webhook-test")
        await expect(sent).to_have_text("Delivered: The receiver answered 200.", timeout=SLOW)
        await expect(sent).to_have_attribute("data-delivered", "true")
        [ping] = wait_for(lambda: received_at(good), "the test message")
        assert json.loads(ping["body"])["type"] == "ping" and check(fine["secret"], ping) is None
        await expect(ok_row.locator(".webhook-state")).to_have_count(0)

        # A receiver that answers with an error: the server's words, in the row.
        await bad_row.locator("button.webhook-test-button").click()
        failed = bad_row.locator(".webhook-test")
        await expect(failed).to_have_text("Not delivered: The receiver answered 500.", timeout=SLOW)
        await expect(failed).to_have_attribute("data-delivered", "false")
        # The test counts as an attempt: the webhook now says it is failing, once, in one chip.
        chip = bad_row.locator(".webhook-state")
        await expect(chip).to_have_count(1, timeout=SLOW)
        await expect(chip).to_have_text("Failing")
        await expect(chip).to_have_attribute("data-state", "failing")
        await chip.focus()
        tip = page.locator(".hovercard:visible")
        await expect(tip).to_contain_text("The receiver answered 500.")
        await expect(tip).to_contain_text("It failed at")
        info = webhook_of(owner, project, broken["webhook"]["id"])
        assert info["failing"] is True and info["last_error"] == "The receiver answered 500."
        # The address never appears in what the page says about the failure.
        assert bad not in (await tip.text_content() or "")
        await page.keyboard.press("Escape")
        assert w.problems == []


async def test_a_test_to_a_slow_address_disables_the_button_and_waiting_is_one_chip() -> None:
    owner, project = await an_owner()
    tag = unique_word()
    slow = add(owner, project, hook("slow", tag), ["ask.raised"])
    number = slow["webhook"]["id"]
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        page = w.page
        await open_users(w)
        one = row(page, number)
        button = one.locator("button.webhook-test-button")
        await button.click()
        # It can take ten seconds: the button says so and cannot be pressed again meanwhile.
        await expect(button).to_have_text("Testing…")
        await expect(button).to_be_disabled()
        await expect(one.locator(".webhook-test")).to_have_text(
            re.compile(r"^Not delivered: "), timeout=40_000
        )
        await expect(row(page, number).locator("button.webhook-test-button")).to_be_enabled()
        # A message that is on its way and has not failed yet is one quiet "Waiting to send" chip.
        owner.call("DELETE", path(project, f"/{number}"))
        again = add(owner, project, hook("slow", unique_word()), ["ask.raised"])
        wait = again["webhook"]["id"]
        await an_ask(agent)
        wait_for(
            lambda: (lambda w_: w_["pending"] == 1 and not w_["failing"])(
                webhook_of(owner, project, wait)
            ),
            "the message to be on its way",
        )
        await w.go("#/board")
        await w.go("#/users")
        chip = row(page, wait).locator(".webhook-state")
        await expect(chip).to_have_text("Waiting to send 1", timeout=SLOW)
        await expect(chip).to_have_attribute("data-state", "waiting")
        assert w.problems == []


# -- changing it ---------------------------------------------------------------------------------


async def test_the_kinds_are_changed_it_is_turned_off_and_on_and_the_secret_is_replaced() -> None:
    owner, project = await an_owner()
    tag = unique_word()
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        page = w.page
        made = add(owner, project, hook("ok", tag), ["ask.raised"])
        number, old = made["webhook"]["id"], made["secret"]
        await open_users(w)
        one = row(page, number)
        await expect(one).to_be_visible(timeout=SLOW)

        # Change kinds: it opens with the current ones ticked, and a choice of none is refused.
        await one.locator("button.webhook-kinds-open").click()
        dialog = page.locator("dialog#change-webhook-dialog[open]")
        await expect(dialog).to_be_visible()
        await nothing_open(page)
        ticked = await dialog.locator("input[name=kind]:checked").evaluate_all(
            "els => els.map(e => e.value)"
        )
        assert ticked == ["ask.raised"]
        await dialog.locator("input[value='ask.raised']").uncheck()
        await dialog.locator("button[type=submit]").click()
        await expect(dialog.locator(".problem")).to_contain_text("events is a list", timeout=SLOW)
        assert webhook_of(owner, project, number)["events"] == ["ask.raised"]
        await dialog.locator("input[value='ask.raised']").check()
        await dialog.locator("input[value='work.done']").check()
        await dialog.locator("button[type=submit]").click()
        await expect(page.locator("dialog#change-webhook-dialog")).to_have_count(0, timeout=SLOW)
        assert webhook_of(owner, project, number)["events"] == ["ask.raised", "work.done"]
        await expect(one.locator(".webhook-kind")).to_have_text(["ask.raised", "work.done"])

        # Turn off: nothing is sent, and the row says so; on again, it is sent.
        await one.locator("button.webhook-switch").click()
        await expect(one).to_have_attribute("data-enabled", "false", timeout=SLOW)
        await expect(one.locator(".webhook-state")).to_have_text("Turned off")
        await expect(one.locator("button.webhook-switch")).to_have_text("Turn on")
        assert webhook_of(owner, project, number)["enabled"] is False
        await an_ask(agent)
        settle()
        assert received_at(tag) == []
        await one.locator("button.webhook-switch").click()
        await expect(one).to_have_attribute("data-enabled", "true", timeout=SLOW)
        await expect(one.locator(".webhook-state")).to_have_count(0)
        assert webhook_of(owner, project, number)["enabled"] is True

        # Replace the secret: a sentence first, then the new one, shown once; the old one signs too.
        await one.locator("button.webhook-secret-open").click()
        confirm = page.locator("dialog#replace-secret-dialog[open]")
        await expect(confirm).to_contain_text("The old secret keeps signing messages too for a day")
        await confirm.locator("button[type=submit]").click()
        new = await the_secret_shown(page)
        assert new != old
        await close_secret(page)
        assert new not in await page.content() and old not in await page.content()
        await expect(one.locator(".webhook-old-secret")).to_have_text(
            re.compile(r"^The old secret still works until \d{4}-\d\d-\d\d\.$"), timeout=SLOW
        )
        await an_ask(agent)
        [message] = wait_for(lambda: received_at(tag), "a message after the change")
        assert len(message["headers"]["webhook-signature"].split()) == 2
        assert check(old, message) is None and check(new, message) is None
        assert w.problems == []


async def test_the_messages_are_listed_and_a_webhook_is_deleted_after_asking() -> None:
    owner, project = await an_owner()
    tag = unique_word()
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        page = w.page
        made = add(owner, project, hook("ok", tag), ["ask.raised"])
        number = made["webhook"]["id"]
        await open_users(w)
        one = row(page, number)
        await one.locator("summary").click()
        await expect(one.locator(".webhook-no-messages")).to_have_text(
            "Nothing has been sent to it yet.", timeout=SLOW
        )
        await an_ask(agent)
        wait_for(lambda: received_at(tag), "the message")
        wait_for(lambda: deliveries(owner, project, number)[0]["state"] == "delivered", "delivered")
        await one.locator("summary").click()
        await one.locator("summary").click()
        served = deliveries(owner, project, number)
        table = one.locator(".webhook-messages-table tbody tr")
        await expect(table).to_have_count(len(served), timeout=SLOW)
        cells = await table.first.locator("td").all_inner_texts()
        assert cells[:5] == ["ask.raised", "Delivered", "1", "200", ""]
        assert await table.evaluate_all("els => els.map(e => e.dataset.message)") == [
            one_["message_id"] for one_ in served
        ]
        head = await one.locator(".webhook-messages-table thead th").all_inner_texts()
        assert [h.lower() for h in head] == [
            "kind",
            "state",
            "tries",
            "the receiver’s answer",
            "what went wrong",
            "when",
        ]
        # Delete asks first, and then it is gone.
        await one.locator("button.webhook-delete").click()
        dialog = page.locator("dialog#delete-webhook-dialog[open]")
        await expect(dialog).to_contain_text("This cannot be undone")
        await dialog.locator("button", has_text="Cancel").click()
        assert len(owner.call("GET", path(project)).body["webhooks"]) == 1
        await one.locator("button.webhook-delete").click()
        await page.locator("dialog#delete-webhook-dialog[open] button[type=submit]").click()
        await expect(one).to_have_count(0, timeout=SLOW)
        await expect(page.locator("#webhooks-empty")).to_be_visible()
        assert owner.call("GET", path(project)).body["webhooks"] == []
        assert w.problems == []


async def test_at_ten_there_is_no_add_button_and_the_section_fits_a_narrow_window() -> None:
    owner, project = await an_owner()
    for index in range(10):
        add(owner, project, hook("ok", f"many-{index}-{unique_word()}"), EVERYTHING[: index + 1])
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await page.set_viewport_size(NARROW)
        await open_users(w)
        await expect(hooks(page)).to_have_count(10, timeout=SLOW)
        await expect(page.locator("#webhook-open")).to_have_count(0)
        await expect(page.locator("#webhooks-full")).to_contain_text("10 webhooks")
        await no_overflow(page)
        # The row's buttons are reached one after another with the keyboard alone.
        first = hooks(page).first
        await first.locator("button.webhook-test-button").focus()
        for wanted in (
            "webhook-kinds-open",
            "webhook-switch",
            "webhook-secret-open",
            "webhook-delete",
        ):
            await page.keyboard.press("Tab")
            await expect(first.locator(f"button.{wanted}")).to_be_focused()
        assert w.problems == []


# -- who has them --------------------------------------------------------------------------------


async def test_an_editor_and_a_viewer_have_no_webhooks_section_and_the_server_refuses_them() -> (
    None
):
    owner, project = await an_owner()
    made = add(owner, project, hook("ok", unique_word()), ["ask.raised"])
    editor = _joined(owner, project, "editor")
    viewer = _joined(owner, project, "viewer")
    for who in (editor, viewer):
        async with browser_page(project.address, token=None, person=who, name=None) as w:
            page = w.page
            await w.open("#/users")
            await on_page(page, "users")
            await expect(page.locator("#users-members")).to_be_visible(timeout=SLOW)
            await expect(page.locator("#users-webhooks")).to_have_count(0)
            await expect(page.get_by_text("Webhooks", exact=True)).to_have_count(0)
            await page.wait_for_load_state("networkidle")
            assert [r for r in w.requests if "/board/api/" in r and "/webhooks" in r] == []
            assert w.problems == []
        for method, tail, body in (
            ("GET", "", None),
            ("POST", "", {"url": hook("ok", unique_word()), "events": ["ask.raised"]}),
            ("PATCH", f"/{made['webhook']['id']}", {"enabled": False}),
            ("DELETE", f"/{made['webhook']['id']}", None),
        ):
            reply = who.call(method, path(project, tail), body)
            assert reply.status in (400, 403, 404), (method, reply.status, reply.body)
    assert webhook_of(owner, project, made["webhook"]["id"])["enabled"] is True


# -- email nudges ----------------------------------------------------------------------------------


async def test_an_address_that_is_not_confirmed_gets_the_words_and_disabled_boxes() -> None:
    owner, project = await an_owner()
    person = _joined(owner, project, "editor", f"{unique_word()}@example.test")
    async with browser_page(project.address, token=None, person=person, name=None) as w:
        page = w.page
        await w.open("#/account")
        await on_page(page, "account")
        section = page.locator("#account-nudges")
        await expect(section.locator(".setting-title")).to_have_text("Email nudges", timeout=SLOW)
        await expect(page.locator("#nudges-unconfirmed")).to_contain_text(
            "Confirm your email address first.", timeout=SLOW
        )
        boxes = section.locator("input[name=kind]")
        await expect(boxes).to_have_count(5)
        for box in await boxes.all():
            await expect(box).to_be_disabled()
        await expect(section.locator("button.nudge-save")).to_be_disabled()
        # The way to confirm is the Email setting above, which the button goes to.
        await page.locator("#nudges-confirm").click()
        await expect(page.locator("#send-verification")).to_be_focused()
        # The server refuses a choice anyway, in words.
        reply = person.call(
            "PUT", f"/board/api/projects/{project.id}/nudges", {"events": ["ask.raised"]}
        )
        assert reply.status == 400 and "confirm" in reply.body["message"].lower(), reply.body
        await no_overflow(page)
        assert w.problems == []


async def test_without_email_the_section_says_so_in_one_sentence_and_has_no_controls() -> None:
    _, project = await an_owner()
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page

        async def no_mail(route: Any) -> None:
            await route.fulfill(
                json={
                    "nudges": [],
                    "kinds": {"ask.raised": "A question."},
                    "mail": False,
                    "email_confirmed": False,
                }
            )

        await page.route("**/board/api/nudges", no_mail)
        await w.open("#/account")
        await on_page(page, "account")
        await expect(page.locator("#nudges-off")).to_have_text(
            "Email is not set up on this server, so nudges cannot be sent.", timeout=SLOW
        )
        await expect(page.locator("#account-nudges input")).to_have_count(0)
        await expect(page.locator("#account-nudges button")).to_have_count(0)
        assert w.problems == []


async def test_a_confirmed_person_saves_the_kinds_and_is_mailed_only_for_them_and_then_stops() -> (
    None
):
    owner, project = await an_owner()
    asker, address = a_person_with_a_confirmed_address(owner, project)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=None, person=asker, name=None) as w,
    ):
        page = w.page
        await w.open("#/account")
        await on_page(page, "account")
        section = page.locator("#account-nudges")
        await expect(section.locator("form.nudge")).to_have_count(1, timeout=SLOW)
        await expect(page.locator("#nudges-unconfirmed")).to_have_count(0)
        served = asker.call("GET", "/board/api/nudges").body
        assert set(served["kinds"]) == {
            "ask.raised",
            "plan.proposed",
            "placement.proposed",
            "work.blocked",
            "claim.lapsed",
        }
        # The project's row: its name, the five kinds with the server's sentences, none ticked.
        form = section.locator(f"form.nudge[data-project='{project.id}']")
        await expect(form.locator(".nudge-name")).to_have_text(project.name)
        boxes = form.locator("input[name=kind]")
        assert await boxes.evaluate_all("els => els.map(e => e.value)") == list(served["kinds"])
        assert await form.locator("input[name=kind]:checked").count() == 0
        for kind, words in served["kinds"].items():
            await expect(form.locator(f"label[for='nudge-{project.id}-{kind}']")).to_have_text(
                words
            )

        # Saved when this row's Save is pressed, and not on every tick.
        await form.locator("input[value='ask.raised']").check()
        await form.locator("input[value='work.blocked']").check()
        assert asker.call("GET", "/board/api/nudges").body["nudges"] == []
        await form.locator("button.nudge-save").click()
        await expect(form.locator(".nudge-saved")).to_have_text(
            "Saved. You will be emailed about 2 kinds of news in this project.", timeout=SLOW
        )
        assert asker.call("GET", "/board/api/nudges").body["nudges"] == [
            {"project": project.id, "events": ["ask.raised", "work.blocked"]}
        ]
        # A visit later shows them ticked.
        await w.go("#/board")
        await w.go("#/account")
        await expect(
            section.locator(f"form.nudge[data-project='{project.id}'] input:checked")
        ).to_have_count(2, timeout=SLOW)

        # The mail comes when an agent raises an ask, to this person, and says nothing written.
        await an_ask(agent)
        mails = wait_for(lambda: wait_for_mail(address, "A question or an approval"), "the mail")
        assert "Which one?" not in mails[0]["text"] and "Needs a decision" not in mails[0]["text"]
        assert "#/needs" in mails[0]["text"]
        wait_for_mail(address, "Work was blocked")

        # Turning them off with an empty choice stops them.
        await form.locator("input[value='ask.raised']").uncheck()
        await form.locator("input[value='work.blocked']").uncheck()
        await form.locator("button.nudge-save").click()
        await expect(form.locator(".nudge-saved")).to_have_text(
            "Saved. You will not be emailed about this project.", timeout=SLOW
        )
        assert asker.call("GET", "/board/api/nudges").body["nudges"] == []
        before = len(sent_to(address, "A question or an approval"))
        await an_ask(agent)
        settle(6)
        assert len(sent_to(address, "A question or an approval")) == before
        assert w.problems == []


async def test_a_refusal_of_a_nudge_is_shown_in_the_servers_words_and_the_page_fits_390_px() -> (
    None
):
    owner, project = await an_owner()
    asker, _ = a_person_with_a_confirmed_address(owner, project)
    async with browser_page(project.address, token=None, person=asker, name=None) as w:
        page = w.page
        await page.set_viewport_size(NARROW)
        await w.open("#/account")
        await on_page(page, "account")
        form = page.locator(f"form.nudge[data-project='{project.id}']")
        await expect(form).to_be_visible(timeout=SLOW)
        # A kind the server does not know (the box is tampered with): refused in its words, here.
        await form.locator("input[value='ask.raised']").evaluate("e => { e.value = 'nonsense'; }")
        await form.locator("input[value='nonsense']").check()
        await form.locator("button.nudge-save").click()
        problem = form.locator(".problem")
        await expect(problem).to_contain_text("nonsense", timeout=SLOW)
        said = asker.call(
            "PUT", f"/board/api/projects/{project.id}/nudges", {"events": ["nonsense"]}
        )
        assert said.status == 400
        plain((await problem.text_content()) or "", said.body["message"])
        assert asker.call("GET", "/board/api/nudges").body["nudges"] == []
        await no_overflow(page)
        # Keyboard: the boxes and Save are reached in order.
        await form.locator("input[value='plan.proposed']").focus()
        await page.keyboard.press("Space")
        await expect(form.locator("input[value='plan.proposed']")).to_be_checked()
        assert w.problems == []
