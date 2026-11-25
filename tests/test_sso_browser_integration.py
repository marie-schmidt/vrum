"""Signing in with another provider (M25) in a real browser: the buttons on the sign-in page, the
redirect out to a provider and back, the one plain problem a person is told when it does not work,
connecting a provider from the Account page, the directory's form, and, against a real OpenID
provider (Dex) with a second copy of the server set up for it, one whole sign-in in Chromium and in
WebKit.

What the server decides is proven in tests/test_sso_integration.py; what is looked at here is what a
person sees and what a browser does with cookies and redirects across two sites. The page is only
told the outcome by a code and the provider in its address, and shows its own words: so a provider's
words, or markup it sends, are never on the page. Chromium is what ./check.sh installs; WebKit is
used when it is installed (`uv run playwright install webkit`) and the test is skipped when it is
not."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, Playwright, expect, sync_playwright

from tests.sso_world import (
    SESSION_COOKIE,
    WRONG_LOGIN,
    Who,
    account_of,
    add_directory_person,
    board_base,
    dex_app_url,
    github,
    new_github_person,
    new_openid_person,
    note_starts,
    oidc,
    relax_limits,
    words,
)
from tests.world import Person, api, make_project, register, server_base

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def quiet_stand_ins() -> Iterator[None]:
    # A page starts at most two or three sign-ins, which the count of the server's limit is told.
    relax_limits()
    note_starts(3)
    oidc().reset()
    github().reset()
    yield
    oidc().reset()
    github().reset()


class Seen:
    """What went wrong in the page that a person would not see: an uncaught error, or a dialog."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.dialogs: list[str] = []


_sync_pw: Playwright | None = None
_sync_browsers: dict[str, Browser] = {}


@pytest.fixture(scope="module", autouse=True)
def _sync_browser_pool() -> Iterator[None]:
    """Share one sync Playwright driver and browser per engine across this module."""
    global _sync_pw
    yield
    for browser in list(_sync_browsers.values()):
        browser.close()
    _sync_browsers.clear()
    if _sync_pw is not None:
        _sync_pw.stop()
        _sync_pw = None


@contextmanager
def browser_page(engine: str = "chromium") -> Iterator[tuple[Page, Seen]]:
    global _sync_pw
    if _sync_pw is None:
        _sync_pw = sync_playwright().start()
    browser = _sync_browsers.get(engine)
    if browser is None or not browser.is_connected():
        try:
            browser = getattr(_sync_pw, engine).launch()
        except Exception as problem:  # noqa: BLE001
            pytest.skip(f"{engine} is not installed here: {str(problem).splitlines()[0]}")
        _sync_browsers[engine] = browser
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    seen = Seen()
    page.on("pageerror", lambda error: seen.errors.append(str(error)))

    def dialog(found: Any) -> None:
        seen.dialogs.append(found.message)
        found.dismiss()

    page.on("dialog", dialog)
    try:
        yield page, seen
    finally:
        context.close()


def session_cookie(page: Page) -> str | None:
    for one in page.context.cookies():
        if one["name"] == SESSION_COOKIE:
            return str(one["value"])
    return None


def who_is_signed_in(page: Page, base: str | None = None) -> dict[str, Any]:
    """The account of the browser's session, as the server's own answer says (waits until the
    session is there)."""
    for _ in range(40):
        cookie = session_cookie(page)
        if cookie:
            reply = api(base or server_base(), "GET", "/board/api/session", cookie=cookie)
            if reply.status == 200 and reply.body["account"]:
                return dict(reply.body["account"])
        page.wait_for_timeout(250)
    pytest.fail("the browser has no session")


def person_of(page: Page, base: str | None = None) -> Person:
    """The person the browser is signed in as, with the secret their page uses for changes."""
    account = who_is_signed_in(page, base)
    cookie = str(session_cookie(page))
    shown = api(base or server_base(), "GET", "/board/api/session", cookie=cookie).body
    return Person(
        base or server_base(),
        str(account["email"]),
        "",
        str(account["name"]),
        cookie,
        shown["csrf"],
        account["id"],
    )


def press_person(page: Page, who: Who | str) -> None:
    key = who if isinstance(who, str) else who.key
    page.locator(f'[data-person="{key}"]').click()


def open_sign_in(page: Page) -> None:
    page.goto(f"{board_base()}/board")
    expect(page.locator("#sso-oidc")).to_be_visible()


def back_on_the_board(page: Page) -> None:
    page.wait_for_url(re.compile(re.escape(board_base()) + r"/board"))


def test_a_person_signs_in_with_openid_in_the_browser_and_is_on_the_board() -> None:
    who = new_openid_person(name="Browser Person")
    with browser_page() as (page, seen):
        open_sign_in(page)
        expect(page.locator("#sso-oidc")).to_contain_text("Sign in with Scripted OpenID")
        expect(page.locator("#sso-github")).to_contain_text("Sign in with GitHub")
        expect(page.locator("#sso-ldap")).to_contain_text("Sign in with Company directory")
        page.locator("#sso-oidc").click()
        # The provider's own page, on its own address, with its people as buttons.
        page.wait_for_url(re.compile(r"/authorize\?"))
        assert page.url.startswith("http://127.0.0.1:")
        expect(page.locator(f'[data-person="{who.key}"]')).to_be_visible()
        press_person(page, who)
        back_on_the_board(page)
        account = who_is_signed_in(page)
        assert account["email"] == who.email and account["name"] == "Browser Person"
        assert account["has_password"] is False
        expect(page.locator("#sso-ways")).to_have_count(0)
        assert "sso" not in page.url
        assert seen.errors == [] and seen.dialogs == []
    assert account_of(str(who.email)) is not None


def test_the_sign_in_page_says_one_plain_problem_and_nothing_from_the_provider() -> None:
    cases: list[tuple[str, Any, str]] = [
        ("alg_none", None, "failed"),
        ("ok", "cancel", "cancelled"),
        ("ok", "server_error", "failed"),
    ]
    with browser_page() as (page, seen):
        for mode, authorize, code in cases:
            who = new_openid_person()
            oidc().control(token=mode)
            if authorize == "server_error":
                oidc().control(authorize="server_error")
            open_sign_in(page)
            page.locator("#sso-oidc").click()
            page.wait_for_url(re.compile(r"/authorize\?"))
            if authorize == "cancel":
                press_person(page, "cancel")
            else:
                press_person(page, who)
            back_on_the_board(page)
            expect(page.get_by_text(words(code, "oidc"), exact=True)).to_be_visible()
            text = page.locator("body").inner_text()
            assert "alert('from the provider')" not in text and "something went wrong" not in text
            # The address no longer carries the outcome, so a reload does not say it again.
            assert "sso_error" not in page.url and "sso_provider" not in page.url
            assert session_cookie(page) is None
            page.reload()
            expect(page.get_by_text(words(code, "oidc"), exact=True)).to_have_count(0)
            expect(page.locator("#sso-oidc")).to_be_visible()
        assert seen.errors == [] and seen.dialogs == []


def test_a_person_with_a_name_that_is_markup_is_shown_as_text() -> None:
    markup = "<img src=x onerror=alert(1)>Eve"
    who = new_openid_person(name=markup)
    with browser_page() as (page, seen):
        open_sign_in(page)
        page.locator("#sso-oidc").click()
        page.wait_for_url(re.compile(r"/authorize\?"))
        press_person(page, who)
        back_on_the_board(page)
        who_is_signed_in(page)
        page.wait_for_timeout(500)
        assert seen.dialogs == [] and seen.errors == []
        assert page.locator("img[src='x']").count() == 0


def test_a_person_signs_in_with_github_after_the_page_says_what_it_asks_for() -> None:
    who = new_github_person()
    with browser_page() as (page, seen):
        open_sign_in(page)
        page.locator("#sso-github").click()
        page.wait_for_url(re.compile(r"/login/oauth/authorize\?"))
        expect(page.locator("body")).to_contain_text("read:user user:email")
        press_person(page, who)
        back_on_the_board(page)
        account = who_is_signed_in(page)
        assert account["email"] == who.email and account["has_password"] is False
        assert seen.errors == [] and seen.dialogs == []


def test_a_github_person_with_no_confirmed_address_is_told_so_in_words() -> None:
    who = new_github_person(
        emails=[{"email": "x@example.com", "verified": False, "primary": True, "visibility": None}]
    )
    with browser_page() as (page, _):
        open_sign_in(page)
        page.locator("#sso-github").click()
        page.wait_for_url(re.compile(r"/login/oauth/authorize\?"))
        press_person(page, who)
        back_on_the_board(page)
        expect(page.get_by_text(words("no_verified_email", "github"), exact=True)).to_be_visible()
        assert session_cookie(page) is None


def test_the_directory_asks_for_a_name_and_password_in_place_and_says_what_goes_where() -> None:
    # A name of its own each time: the directory keeps the people it was given.
    who = add_directory_person()
    with browser_page() as (page, seen):
        open_sign_in(page)
        expect(page.locator("#ldap-form")).to_be_hidden()
        page.locator("#sso-ldap").click()
        expect(page.locator("#ldap-form")).to_be_visible()
        expect(page.locator("#ldap-form")).to_contain_text("checks it with the directory")
        page.locator("#ldap-username").fill(who.key)
        page.locator("#ldap-password").fill("wrong-password")
        page.locator("#ldap-submit").click()
        expect(page.locator("#ldap-problem")).to_have_text(WRONG_LOGIN)
        assert session_cookie(page) is None
        page.locator("#ldap-password").fill(str(who.password))
        page.locator("#ldap-submit").click()
        account = who_is_signed_in(page)
        assert account["email"] == who.email and account["name"] == who.name
        assert str(who.password) not in page.content() and page.url.startswith(board_base())
        assert seen.errors == [] and seen.dialogs == []


def test_a_signed_in_person_connects_and_sees_who_they_are_connected_as_on_the_account_page() -> (
    None
):
    person = register()
    project = make_project(person)
    who = new_openid_person(name="Connected Name")
    with browser_page() as (page, seen):
        page.context.add_cookies(
            [
                {
                    "name": SESSION_COOKIE,
                    "value": person.cookie,
                    "url": f"{board_base()}/board",
                    "httpOnly": True,
                    "sameSite": "Strict",
                }
            ]
        )
        page.goto(f"{board_base()}/board/p/{project.id}#/account")
        # A way that is not connected says nothing but its button, and so does a password that is
        # set: the state is only said when it is something to know.
        expect(page.locator("#connect-oidc")).to_be_visible()
        expect(page.locator("#method-oidc-state")).to_have_count(0)
        expect(page.locator("#method-password-state")).to_have_count(0)
        expect(page.locator("#change-password")).to_be_visible()
        page.locator("#connect-oidc").click()
        page.wait_for_url(re.compile(r"/authorize\?"))
        press_person(page, who)
        page.wait_for_url(re.compile(re.escape(f"{board_base()}/board/p/{project.id}")))
        # The person comes back to the Account page, where the way is now connected, as whoever
        # the provider says the person is: their address or their name.
        expect(page.locator("main#page h1")).to_have_text("Account")
        assert page.url.endswith("#/account"), page.url
        expect(page.locator("#method-oidc-state")).to_have_text(
            re.compile(rf"^Connected as ({re.escape(str(who.email))}|Connected Name)$")
        )
        assert "sso=" not in page.url and "sso_error" not in page.url
        expect(page.locator("#disconnect-oidc")).to_be_visible()
        expect(page.locator("#connect-oidc")).to_have_count(0)
        assert seen.errors == [] and seen.dialogs == []
    reply = api(server_base(), "GET", "/board/api/account/identities", cookie=person.cookie)
    assert [one["provider"] for one in reply.body["identities"]] == ["oidc"]


def test_a_person_with_no_password_is_told_how_they_sign_in_and_can_set_one() -> None:
    who = new_openid_person()
    with browser_page() as (page, seen):
        open_sign_in(page)
        page.locator("#sso-oidc").click()
        page.wait_for_url(re.compile(r"/authorize\?"))
        press_person(page, who)
        back_on_the_board(page)
        person = person_of(page)
        assert person.email == who.email
        project = make_project(person, "Browser project")
        page.goto(f"{board_base()}/board/p/{project.id}#/account")
        expect(page.locator("#method-password-state")).to_contain_text("You have no password")
        expect(page.locator("#method-password-state")).to_contain_text("Scripted OpenID")
        expect(page.locator("#set-password")).to_be_visible()
        assert seen.errors == [] and seen.dialogs == []


@pytest.mark.parametrize("engine", ["chromium", "webkit"])
def test_one_whole_sign_in_against_a_real_openid_provider_that_is_not_scripted(engine: str) -> None:
    base = dex_app_url()
    with browser_page(engine) as (page, seen):
        page.goto(f"{base}/board")
        expect(page.locator("#sso-oidc")).to_contain_text("Sign in with Dex")
        expect(page.locator("#sso-github")).to_have_count(0)
        expect(page.locator("#sso-ldap")).to_have_count(0)
        page.locator("#sso-oidc").click()
        # Dex's own page, on an address that means this machine to the browser and, through the
        # network alias, Dex to the server.
        page.wait_for_url(re.compile(r"dex\.localhost:\d+/dex/auth"))
        page.locator("#login").fill("dana.dex@example.com")
        page.locator("#password").fill("open-sesame-dana")
        page.locator("#submit-login").click()
        page.wait_for_url(re.compile(re.escape(base) + r"/board"))
        account = who_is_signed_in(page, base)
        assert account["email"] == "dana.dex@example.com"
        assert account["email_verified"] is True and account["has_password"] is False
        assert "sso_error" not in page.url
        expect(page.locator("#sso-ways")).to_have_count(0)
        assert seen.errors == [] and seen.dialogs == []


def test_a_wrong_password_at_the_real_provider_never_reaches_vrum() -> None:
    base = dex_app_url()
    with browser_page() as (page, _):
        page.goto(f"{base}/board")
        page.locator("#sso-oidc").click()
        page.wait_for_url(re.compile(r"dex\.localhost:\d+/dex/auth"))
        page.locator("#login").fill("dev.dex@example.com")
        page.locator("#password").fill("not-the-password")
        page.locator("#submit-login").click()
        page.wait_for_timeout(1500)
        assert "dex.localhost" in page.url and session_cookie(page) is None
