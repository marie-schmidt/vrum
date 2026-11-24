"""M17, proof 3: Light, Dark and System, in a real headless browser against the real containers.

The three choices are tried before sign-in and after it, through a reload, signing out and
switching project; the operating system's preference is changed while a page is open (only System
follows it) and a fresh browser with no saved choice follows it; what is painted first is checked
against the resolved theme, with a real screenshot of the first frame; the security policy is
unchanged; switching appearance keeps a draft, the filters and the scroll of the page and neither
navigates nor loads anything (there is no panel and no drawer: every record, Search and Activity
are pages, themed like the rest); and every surface takes its colours from the tokens of the
theme (read from tokens.css), the two themes differ, and nothing is inverted mechanically."""

import asyncio
import contextlib
import re
from typing import Any
from urllib.parse import parse_qs

import pytest
from playwright.async_api import Page, Route, expect

from tests.board_helpers import (
    SLOW,
    hold_back_news,
    http,
    item_address,
    made,
    open_account_menu,
    open_editor,
    sign_out,
)
from tests.board_layout import rested
from tests.board_theme import (
    CHOICES,
    FIRST_PAINT,
    POLICY_WATCH,
    THEMES,
    api_requests,
    at,
    choose,
    colour_tokens,
    entry,
    foreign_colours,
    in_project,
    look,
    luminance,
    os_prefers,
    painted,
    palette,
    parse_colour,
    pixels,
    policy_violations,
    project_list,
    rgb_of,
    settled,
    stored_in_browser,
    survey,
    switch,
    themed_page,
    token,
    transitions_done,
)
from tests.helpers import (
    code_of,
    connect,
    make_note,
    number_of,
    ok,
    ref_from_code,
    ref_of,
    rich_world,
    unique_word,
)
from tests.world import Project, make_project, register
from vrum.board import SECURITY_HEADERS

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
# What the board may ask the server while only the appearance changes: its own poll.
POLL = {"tool/changes_since", "index"}
Painted = dict[str, dict[str, dict[str, str] | None]]


def other(theme: str) -> str:
    return "dark" if theme == "light" else "light"


def the_choice(choice: str, system: str) -> str:
    """The theme a choice shows when the operating system prefers `system`."""
    return system if choice == "system" else choice


@pytest.fixture
def base(server_url: str) -> str:
    return server_url.removesuffix("/mcp")


@pytest.fixture
def project(base: str) -> Project:
    return make_project(register(base))


async def no_credentials(page: Page, secrets: list[str]) -> None:
    """The only things this browser keeps for the page are the appearance and the name that changes
    are recorded with: no session, no secret of a session and no agent key is stored beside it."""
    kept = await stored_in_browser(page)
    assert set(kept["local"]) <= {"vrum.theme", "vrum.actor"}, kept
    assert kept["local"].get("vrum.theme", "light") in {"light", "dark"}, kept
    assert kept["session"] == {} and kept["visibleCookies"] == "", kept
    for value in kept["local"].values():
        for secret in secrets:
            assert secret and secret not in value, (secret[:4], kept)
    assert not [name for name in kept["cookies"] if "theme" in name or "appearance" in name]


# -- before sign-in: the control, each choice, a reload, and what is stored ------------------------


@pytest.mark.parametrize("scheme", THEMES)
async def test_before_sign_in_each_choice_applies_survives_a_reload_and_is_stored_alone(
    base: str, scheme: str
) -> None:
    async with themed_page(f"{base}/board", signed_in=False, scheme=scheme, name=None) as w:
        page = w.page
        await entry(page, f"{base}/board")
        group = page.get_by_role("radiogroup", name="Appearance")
        await expect(group).to_have_count(1)
        await expect(group.get_by_role("radio")).to_have_count(3)
        assert await group.locator("label").all_inner_texts() == ["Light", "Dark", "System"]
        # Each radio has a name a screen reader can say.
        for name in ("Light", "Dark", "System"):
            await expect(group.get_by_role("radio", name=name)).to_have_count(1)
        # No saved choice: System, which is the operating system's.
        await settled(page, scheme, "system")
        assert (await stored_in_browser(page))["local"] == {}

        for choice in ("dark", "light", "system", "dark", "system", "light"):
            await choose(page, choice)
            theme = the_choice(choice, scheme)
            await settled(page, theme, choice)
            kept = await stored_in_browser(page)
            wanted = {} if choice == "system" else {"vrum.theme": choice}
            assert kept["local"] == wanted, kept
            assert kept["session"] == {} and kept["visibleCookies"] == "" and kept["cookies"] == []
            await page.reload()
            await page.wait_for_selector(".entry", timeout=SLOW)
            await settled(page, theme, choice)
            assert (await stored_in_browser(page))["local"] == wanted

        # The control works from the keyboard: the arrows move through the three and each applies.
        await page.locator("[data-appearance=light]").focus()
        for choice in ("dark", "system", "light"):
            await page.keyboard.press("ArrowRight")
            await settled(page, the_choice(choice, scheme), choice)
        assert w.problems == [], w.problems
        assert await policy_violations(page) == []
        assert not [url for url in w.requests if not url.startswith(base)], w.requests


# -- signing in, the project list, switching project, reload and signing out -----------------------


async def test_the_choice_survives_signing_in_project_switching_reload_and_signing_out(
    base: str,
) -> None:
    owner = register(base)
    first = make_project(owner, "First appearance")
    second = make_project(owner, "Second appearance")
    async with themed_page(first.address, signed_in=False, scheme="light", name=None) as w:
        page = w.page
        await entry(page, first.address)
        await choose(page, "dark")
        await settled(page, "dark", "dark")

        # Sign in with the real form: the page loads the project again, still dark.
        await page.locator("#account-email").fill(owner.email)
        await page.locator("#account-password").fill(owner.password)
        await page.locator("#sign-in-submit").click()
        await page.wait_for_selector("#app[data-shell=project]", timeout=SLOW)
        await settled(page, "dark", "dark")
        # The choice is in the account menu now (the top bar has none), shows the choice, and
        # there is only one group of choices on the page.
        assert (await look(page))["controls"] == 1
        assert await page.locator("#top .appearance, #top [data-appearance]").count() == 0
        await expect(page.locator("#account-appearance-dark")).to_have_attribute(
            "aria-checked", "true"
        )
        cookies = {each["name"]: each["value"] for each in await page.context.cookies()}
        assert list(cookies) == ["vrum_session"]
        session = await page.evaluate("fetch('/board/api/session').then((r) => r.json())")
        secrets = [cookies["vrum_session"], session["csrf"], first.key, second.key]
        await no_credentials(page, secrets)
        assert (await stored_in_browser(page))["local"]["vrum.theme"] == "dark"

        # The person's own project list, signed in: the top bar has the control, still dark.
        await project_list(page, f"{base}/board")
        await settled(page, "dark", "dark")
        await expect(page.locator("#top [data-appearance=dark]")).to_be_checked()

        # Another project is a whole new page, in the same appearance; a change there is kept.
        await in_project(page, first.address)
        await page.locator("#project-switcher").select_option(label="Second appearance")
        await page.wait_for_url(re.compile(rf"/board/p/{second.id}(#.*)?$"), timeout=SLOW)
        await page.wait_for_selector("#app[data-shell=project]", timeout=SLOW)
        await settled(page, "dark", "dark")
        await choose(page, "light")
        await settled(page, "light", "light")
        await page.locator("#project-switcher").select_option(label="First appearance")
        await page.wait_for_url(re.compile(rf"/board/p/{first.id}(#.*)?$"), timeout=SLOW)
        await page.wait_for_selector("#app[data-shell=project]", timeout=SLOW)
        await settled(page, "light", "light")
        await page.reload()
        await page.wait_for_selector("#app[data-shell=project]", timeout=SLOW)
        await settled(page, "light", "light")
        await no_credentials(page, secrets)

        # Signing out keeps the choice: the page that comes back is as the person left it.
        await choose(page, "dark")
        await sign_out(page)
        await page.wait_for_selector(".entry", timeout=SLOW)
        await settled(page, "dark", "dark")
        await expect(page.locator("#top [data-appearance=dark]")).to_be_checked()
        kept = await stored_in_browser(page)
        assert kept["local"]["vrum.theme"] == "dark" and kept["cookies"] == [], kept
        await no_credentials(page, secrets)

        # Signed in again, it is still Dark; System then follows the (light) system.
        await page.locator("#account-email").fill(owner.email)
        await page.locator("#account-password").fill(owner.password)
        await page.locator("#sign-in-submit").click()
        await page.wait_for_selector("#projects, #no-projects", timeout=SLOW)
        await settled(page, "dark", "dark")
        await choose(page, "system")
        await settled(page, "light", "system")
        assert (await stored_in_browser(page))["local"].get("vrum.theme") is None
        assert w.problems == [], w.problems
        assert await policy_violations(page) == []


# -- the system's preference changes while a page is open: only System follows ---------------------

LISTEN = """() => {
  window.__events = [];
  window.addEventListener('vrum-theme', (event) => window.__events.push(event.detail));
}"""


@pytest.mark.parametrize("signed_in", [False, True])
async def test_only_system_follows_the_operating_system_while_the_page_is_open(
    base: str, project: Project, signed_in: bool
) -> None:
    address = project.address if signed_in else f"{base}/board"
    for start in THEMES:
        async with themed_page(address, signed_in=signed_in, scheme=start, name=None) as w:
            page = w.page
            await at(page, signed_in, address)
            await page.evaluate(LISTEN)
            for choice in CHOICES:
                await choose(page, choice)
                await settled(page, the_choice(choice, start), choice)
                for scheme in (other(start), start, other(start)):
                    await os_prefers(page, scheme)
                    # Light and Dark stay as they are; System goes where the system went.
                    await settled(page, the_choice(choice, scheme), choice)
                    state = await look(page)
                    assert state["os"] == scheme
                    assert (state["stored"] or "system") == choice
                events = await page.evaluate("window.__events")
                heard = [event for event in events if event["choice"] == choice]
                assert heard, events
                expected = {choice} if choice != "system" else set(THEMES)
                assert {event["theme"] for event in heard} == expected, (choice, heard)
                await os_prefers(page, start)
            assert w.problems == [], w.problems


# -- a fresh browser with no saved choice follows the system ---------------------------------------


@pytest.mark.parametrize("signed_in", [False, True])
async def test_a_fresh_browser_with_no_saved_choice_follows_the_operating_system(
    base: str, project: Project, signed_in: bool
) -> None:
    address = project.address if signed_in else f"{base}/board"
    for scheme in (*THEMES, "no-preference"):
        async with themed_page(address, signed_in=signed_in, scheme=scheme, name=None) as w:
            page = w.page
            await at(page, signed_in, address)
            await settled(page, "dark" if scheme == "dark" else "light", "system")
            assert "vrum.theme" not in (await stored_in_browser(page))["local"]
            await expect(page.locator("[data-appearance=system]")).to_be_checked()
            if scheme != "no-preference":
                await os_prefers(page, other(scheme))
                await settled(page, other(scheme), "system")
                await os_prefers(page, scheme)
                await settled(page, scheme, "system")
            assert w.problems == [], w.problems

    # A choice is kept in the browser that made it and nowhere else: not on the server, not in the
    # account, so a second fresh browser (even of the same signed-in person) is back to System.
    async with themed_page(address, signed_in=signed_in, scheme="dark", name=None) as w:
        await at(w.page, signed_in, address)
        await choose(w.page, "light")
        await settled(w.page, "light", "light")
    async with themed_page(address, signed_in=signed_in, scheme="dark", name=None) as w:
        await at(w.page, signed_in, address)
        await settled(w.page, "dark", "system")
        assert "vrum.theme" not in (await stored_in_browser(w.page))["local"]


# -- the first frame -------------------------------------------------------------------------------

COMBINATIONS = [("dark", None), ("light", None), ("light", "dark"), ("dark", "light")]


def at_first_paint(record: dict[str, Any], resolved: str, *, loaded: bool = True) -> None:
    """The document said `resolved` before the first paint and never changed its mind: nothing was
    painted, or measured, in the other theme."""
    paints = [each["time"] for each in record["paints"]]
    assert paints, record
    first = min(paints)
    changes = record["changes"]
    # The page file says light until the script in the head has run (the first change); from then
    # on it is the resolved theme, and that was settled before anything could be painted.
    assert len(changes) >= 2, changes
    assert [each["theme"] for each in changes[1:]] == [resolved] * (len(changes) - 1), changes
    assert [each for each in changes if each["time"] <= first][-1]["theme"] == resolved
    assert all(each["time"] < first for each in changes[1:]), (first, changes)
    frame = record["frame"]
    assert frame is not None and frame["time"] <= first, (frame, first)
    for moment in (frame, record["loaded"] if loaded else None):
        if moment is None:
            continue
        assert moment["theme"] == resolved and moment["scheme"] == resolved, moment
        background = parse_colour(moment["bodyBackground"])
        assert background is not None and background[:3] == token(resolved, "--bg"), moment
        assert (luminance(background) < 0.1) == (resolved == "dark"), moment
        assert (luminance(background) > 0.8) == (resolved == "light"), moment
        assert parse_colour(moment["htmlBackground"]) in (None, (0, 0, 0, 0.0)), moment


@pytest.mark.parametrize("signed_in", [False, True])
@pytest.mark.parametrize(("system", "saved"), COMBINATIONS)
async def test_the_resolved_theme_is_set_before_the_first_paint_with_no_wrong_theme_flash(
    base: str, project: Project, signed_in: bool, system: str, saved: str | None
) -> None:
    address = project.address if signed_in else f"{base}/board"
    resolved = saved or system
    async with themed_page(
        address, signed_in=signed_in, scheme=system, name=None, scripts=(FIRST_PAINT, POLICY_WATCH)
    ) as w:
        page = w.page
        await at(page, signed_in, address)
        if saved:
            await choose(page, saved)
        for _ in range(2):
            await page.reload()
            await page.wait_for_selector("#app[data-shell=project], .entry", timeout=SLOW)
            at_first_paint(await page.evaluate("window.__first"), resolved)
            await settled(page, resolved, saved or "system")
        assert w.problems == [], w.problems
        assert await policy_violations(page) == []


@pytest.mark.parametrize(("system", "saved"), COMBINATIONS)
async def test_a_real_screenshot_of_the_first_frame_is_in_the_resolved_theme(
    project: Project, system: str, saved: str | None
) -> None:
    resolved = saved or system
    async with themed_page(
        project.address, signed_in=True, scheme=system, name=None, scripts=(FIRST_PAINT,)
    ) as w:
        page = w.page
        await in_project(page, project.address)
        if saved:
            await choose(page, saved)

        # The page's script is held back, so that what is on screen is what the page file, the
        # theme script and the style sheets paint on their own: the very first frame.
        release = asyncio.Event()

        async def hold(route: Route) -> None:
            await release.wait()
            with contextlib.suppress(Exception):
                await route.continue_()

        await page.route("**/board/app.js", hold)
        await page.goto(project.address, wait_until="commit")
        await page.wait_for_function("() => performance.getEntriesByType('paint').length > 0")
        await expect(page.locator("#page .loading")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#app")).to_have_attribute("data-shell", "loading")
        shot = await page.screenshot(type="png")
        state = await look(page)
        record = await page.evaluate("window.__first")
        release.set()
        await page.wait_for_selector("#app[data-shell=project]", timeout=SLOW)

        assert state["theme"] == resolved and state["scheme"] == resolved, state
        at_first_paint(record, resolved, loaded=False)
        # Every corner and the middle of the picture are the theme's page colour.
        size = 1500
        points = [
            (2, 2),
            (size - 3, 2),
            (2, size - 3),
            (size - 3, size - 3),
            (size // 2, size // 2),
        ]
        wanted = token(resolved, "--bg")
        assert await pixels(page, shot, points) == [wanted] * len(points)
        assert (luminance(wanted) < 0.1) == (resolved == "dark")
        await settled(page, resolved, saved or "system")


# -- no content security policy exception ----------------------------------------------------------


async def test_the_security_policy_is_unchanged_and_the_theme_script_is_an_external_file(
    base: str,
) -> None:
    assert SECURITY_HEADERS["Content-Security-Policy"] == POLICY
    for path in ("/board", "/board/", "/board/theme.js", "/board/tokens.css", "/board/base.css"):
        status, headers, _ = http("GET", f"{base}{path}")
        assert status == 200, path
        assert headers["content-security-policy"] == POLICY, path
        assert "unsafe" not in headers["content-security-policy"], path
        assert not re.search(r"nonce-|sha\d+-", headers["content-security-policy"]), path
    _, _, page = http("GET", f"{base}/board")
    head = page.split("<head>", 1)[1].split("</head>", 1)[0]
    # The theme is decided by the one file the policy already allows, never by a script written
    # into the page, and nothing in the page is styled in its own markup.
    assert re.findall(r"<script\b[^>]*>", head)[0] == '<script src="/board/theme.js">'
    assert not re.search(r"<script(?![^>]*\ssrc=)", page)
    assert "<style" not in page and " style=" not in page and not re.search(r"\son\w+=", page)
    _, headers, script = http("GET", f"{base}/board/theme.js")
    assert headers["content-type"].startswith("text/javascript") and "vrum.theme" in script


async def test_switching_appearance_across_every_page_causes_no_policy_violation(
    base: str, project: Project
) -> None:
    async with connect(project.mcp_url, project.key) as client:
        world = await rich_world(client, project)
    story, task = world["story1"]["ref"], world["task1"]["ref"]
    fact = ref_of("note", world["fact1"]["id"])
    decision = ref_of("note", world["decision2"]["id"])
    rule = ref_of("rule", world["rule1"]["id"])
    async with themed_page(project.address, signed_in=True, scheme="light") as w:
        page = w.page
        await in_project(page, f"{project.address}#/board")
        for address in (
            "#/search?search=log",
            "#/search?dups=1",
            "#/activity",
            "#/coverage",
            item_address(story),
            f"{item_address(task)}&from=graph",
            item_address(world["epic"]["ref"]),
            f"{item_address(rule)}&from=coverage",
            f"{item_address(decision)}&from=search",
            f"{item_address(fact)}&from=activity",
            "#/graph",
            f"#/graph?tab=around&focus={story}",
            "#/graph?tab=order",
            "#/lists?view=everything",
            "#/roadmap",
            "#/users",
            "#/tools",
            "#/status",
        ):
            await w.go(address)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(300)
            for choice in ("dark", "light", "system"):
                await choose(page, choice)
            assert await policy_violations(page) == [], address
        await w.go("#/board")
        await page.locator("#new-item").click()
        await expect(page.locator("dialog.modal")).to_be_visible()
        await page.evaluate("window.vrumTheme.set('dark')")
        await page.evaluate("window.vrumTheme.set('light')")
        assert await policy_violations(page) == []
        assert w.problems == [], w.problems
        assert not [url for url in w.requests if not url.startswith(base)], w.requests


# -- switching appearance while editing ------------------------------------------------------------

BOARD_STATE = """() => {
  const field = (id) => document.getElementById(id);
  return {
    hash: location.hash,
    history: history.length,
    filter: [field('filter').value, field('filter').__same],
    cards: [...document.querySelectorAll('.card')].map((e) => e.dataset.ref),
    search: [field('global-search').value, field('global-search').__same],
    board: window.__board === document.querySelector('.board-inner'),
    beside: document.querySelectorAll('#panel, #drawer').length,
    scroll: [field('board').scrollLeft, field('board').scrollTop],
  };
}"""

ITEM_STATE = """() => {
  const field = (id) => document.getElementById(id);
  return {
    hash: location.hash,
    history: history.length,
    heading: field('item-title').textContent,
    page: window.__page === field('item-page'),
    title: [field('edit-title').value, field('edit-title').__same],
    body: [field('edit-body').value, field('edit-body').__same],
    caret: [field('edit-body').selectionStart, field('edit-body').selectionEnd],
    priority: [field('edit-priority').value, field('edit-priority').__same],
    target: field('edit-target').value,
    search: [field('global-search').value, field('global-search').__same],
    beside: document.querySelectorAll('#panel, #drawer').length,
  };
}"""


async def test_switching_appearance_keeps_the_draft_filters_and_scroll_of_a_page_and_loads_nothing(
    project: Project,
) -> None:
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Epic {word}")
        story = await made(client, "story", f"Story {word}", epic, body="The story's own words.")
        task = await made(client, "task", f"Task {word}", story)
        await made(client, "task", f"Another task {word}", story, priority="high")
        await made(client, "epic", f"Other epic {word}")
        version = (await ok(client, "get_node", node=task))["version"]

    async with themed_page(project.address, signed_in=True, scheme="light") as w:
        page = w.page
        # A window narrow enough that the Board's columns scroll along their lane, and wide enough
        # that the sidebar is still a column of the page.
        await page.set_viewport_size({"width": 1300, "height": 1500})
        await in_project(page, f"{project.address}#/board?epic={number_of(epic)}&q=kind:task")
        await expect(page.locator(".card")).to_have_count(2, timeout=SLOW)
        await hold_back_news(page)

        # Words typed into the filter box and the top bar's box and not sent, and marks to know
        # the elements again by.
        await page.locator("#filter").fill("kind:task status:done")
        await page.locator("#global-search").fill("words not yet searched")
        await page.evaluate(
            """() => {
                document.getElementById('filter').__same = 'filter';
                document.getElementById('global-search').__same = 'search';
                document.getElementById('board').scrollLeft = 90;
                window.__board = document.querySelector('.board-inner');
            }"""
        )
        before: dict[str, Any] = await page.evaluate(BOARD_STATE)
        assert before["filter"] == ["kind:task status:done", "filter"] and before["board"]
        assert before["search"] == ["words not yet searched", "search"] and before["beside"] == 0
        # The Board is scrolled along its lane, and stays so.
        assert before["scroll"][0] > 0, before["scroll"]
        asked = len(w.requests)

        for choice in ("dark", "light", "system", "dark", "light"):
            await choose(page, choice)
            await settled(page, the_choice(choice, "light"), choice)

        after: dict[str, Any] = await page.evaluate(BOARD_STATE)
        # The address and the history did not move, nothing was drawn again, and what was typed is
        # still in the fields it was typed in.
        assert after == before
        choices = parse_qs(after["hash"].split("?", 1)[1])
        assert choices == {"epic": [str(number_of(epic))], "q": ["kind:task"]}
        # The board asked for nothing but its poll while the appearance was switched.
        assert set(api_requests(w.requests[asked:])) <= POLL, w.requests[asked:]

        # The page of a task, with a draft in its form and in the top bar's box.
        await w.go(f"{item_address(task)}&from=board")
        await expect(page.locator("#item-page")).to_have_attribute("data-ref", task, timeout=SLOW)
        await expect(page.locator("#item-title")).to_contain_text(f"{code_of(task)} ")
        await open_editor(page)
        await page.locator("#edit-title").fill("A draft title ✎ not saved")
        await page.locator("#edit-body").fill("Draft line one\nDraft line two with [[task:1]]")
        await page.locator("#edit-priority").select_option("high")
        await page.locator("#edit-target").fill("2027-03-04")
        await page.locator("#global-search").fill("words typed in the bar")
        await page.locator("#edit-body").focus()
        await page.evaluate(
            """() => {
                for (const id of ['edit-title', 'edit-body', 'edit-priority', 'global-search']) {
                    document.getElementById(id).__same = id;
                }
                document.getElementById('edit-body').setSelectionRange(5, 9);
                window.__page = document.getElementById('item-page');
            }"""
        )
        item_before: dict[str, Any] = await page.evaluate(ITEM_STATE)
        assert item_before["caret"] == [5, 9] and item_before["page"]
        assert item_before["heading"].startswith(code_of(task))
        asked = len(w.requests)
        for choice in ("dark", "light", "system", "dark", "light"):
            await choose(page, choice)
            await settled(page, the_choice(choice, "light"), choice)
        item_after: dict[str, Any] = await page.evaluate(ITEM_STATE)
        # What was typed is still in the fields it was typed in, with the same selected text.
        assert item_after == item_before
        assert item_after["title"] == ["A draft title ✎ not saved", "edit-title"]
        assert item_after["body"] == ["Draft line one\nDraft line two with [[task:1]]", "edit-body"]
        assert item_after["priority"] == ["high", "edit-priority"]
        assert item_after["target"] == "2027-03-04" and item_after["beside"] == 0
        assert item_after["search"] == ["words typed in the bar", "global-search"]
        assert set(api_requests(w.requests[asked:])) <= POLL, w.requests[asked:]
        # Nothing was saved: the task is as it was.
        async with connect(project.mcp_url, project.key) as client:
            assert (await ok(client, "get_node", node=task))["version"] == version

        # The Graph keeps what is looked at, its tab and the places of the boxes around it.
        await w.go(f"#/graph?tab=around&focus={story}")
        await expect(page.locator("#graph-hub")).to_have_attribute("data-ref", story, timeout=SLOW)
        boxes = await settled_boxes(page)
        for choice in ("dark", "light"):
            await choose(page, choice)
            await settled(page, choice, choice)
        assert await settled_boxes(page) == boxes
        await expect(page.locator("#graph-hub")).to_have_attribute("data-ref", story)
        assert (await page.evaluate("location.hash")).startswith("#/graph?")

        # The Roadmap keeps its zoom and where the timeline is scrolled to.
        await w.go("#/roadmap")
        await expect(page.locator("#rm-scroll")).to_be_visible(timeout=SLOW)
        timeline = page.locator("#rm-scroll")
        spot = await timeline.evaluate(
            """(el) => {
                el.scrollLeft = 60;
                return [el.scrollLeft, el.scrollWidth > el.clientWidth];
            }"""
        )
        assert spot == [60, True], spot
        zoom = await page.locator(".segmented button[aria-pressed=true]").all_inner_texts()
        roadmap_hash = await page.evaluate("location.hash")
        for choice in ("dark", "light"):
            await choose(page, choice)
            await settled(page, choice, choice)
        assert await timeline.evaluate("(el) => el.scrollLeft") == 60
        assert await page.locator(".segmented button[aria-pressed=true]").all_inner_texts() == zoom
        assert (await page.evaluate("location.hash")) == roadmap_hash == "#/roadmap"
        assert w.dialogs == [] and w.problems == [], w.problems
        assert await policy_violations(page) == []


async def settled_boxes(page: Page) -> list[tuple[Any, ...]]:
    """Where the chosen item and every box around it are in the Graph's Around tab, once they have
    stopped moving."""
    last: list[tuple[Any, ...]] = []
    for _ in range(40):
        found: list[list[Any]] = await page.locator("#graph-hub, .graph-gbox").evaluate_all(
            """els => els.map((e) => {
                const box = e.getBoundingClientRect();
                return [e.id || e.dataset.group, box.x, box.y, box.width, box.height];
            })"""
        )
        now = [tuple(each) for each in found]
        if now and now == last:
            return now
        last = now
        await page.wait_for_timeout(250)
    return last


async def check_dialog(page: Page, word: str) -> None:
    dialog = page.locator("dialog.modal")
    await expect(dialog).to_be_visible()
    assert await dialog.locator("#new-title").input_value() == f"Typed in a dialog {word}"
    assert await dialog.locator("#new-body").input_value() == "Two lines\nin the details"
    assert await dialog.locator("#new-priority").input_value() == "high"
    assert await dialog.locator("#new-start").input_value() == "2027-01-05"
    assert await dialog.locator("#new-kind").input_value() == "story"
    assert await page.evaluate("document.getElementById('new-title').__same") == "title"
    assert await page.evaluate("document.activeElement.id") == "new-body"
    assert await page.evaluate("document.querySelectorAll('dialog[open]').length") == 1


async def test_a_dialog_keeps_what_was_typed_when_another_tab_or_the_system_changes_the_theme(
    project: Project,
) -> None:
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Home epic {word}")
    async with themed_page(project.address, signed_in=True, scheme="light") as w:
        page = w.page
        await in_project(page, f"{project.address}#/board")
        await page.locator("#new-item").click()
        dialog = page.locator("dialog.modal")
        await expect(dialog).to_be_visible()
        await dialog.locator("#new-kind").select_option("story")
        await dialog.locator("#new-parent").select_option(epic)
        await dialog.locator("#new-title").fill(f"Typed in a dialog {word}")
        await dialog.locator("#new-body").fill("Two lines\nin the details")
        await dialog.locator("#new-priority").select_option("high")
        await dialog.locator("#new-start").fill("2027-01-05")
        await dialog.locator("#new-body").focus()
        await page.evaluate("document.getElementById('new-title').__same = 'title'")
        heard: list[str] = []
        page.on("request", lambda request: heard.append(request.url))
        hash_before = await page.evaluate("location.hash")

        # Another tab of the same browser chooses Dark: this page hears of it (the `storage` event)
        # while its dialog is open, and changes colour without touching the dialog.
        tab = await page.context.new_page()
        await in_project(tab, f"{project.address}#/board")
        await choose(tab, "dark")
        await settled(page, "dark", "dark")
        await tab.close()
        await transitions_done(page)
        found = await painted(page, {"dialog": "dialog.modal"})
        assert found["dialog"] is not None
        assert rgb_of(found["dialog"]["background"]) == token("dark", "--surface")
        assert parse_colour(found["dialog"]["backdrop"] or "") == colour_tokens("dark")["--scrim"]
        await check_dialog(page, word)

        # Then System, and the system goes dark and light again: it is still the dialog typed in.
        await page.evaluate("window.vrumTheme.set('system')")
        await os_prefers(page, "dark")
        await settled(page, "dark", "system")
        await os_prefers(page, "light")
        await settled(page, "light", "system")
        await transitions_done(page)
        found = await painted(page, {"dialog": "dialog.modal"})
        assert found["dialog"] is not None
        assert rgb_of(found["dialog"]["background"]) == token("light", "--surface")
        assert parse_colour(found["dialog"]["backdrop"] or "") == colour_tokens("light")["--scrim"]
        await check_dialog(page, word)
        assert await page.evaluate("location.hash") == hash_before
        # This page asked for nothing but its poll while the appearance changed under it.
        assert set(api_requests(heard)) <= POLL, heard

        # It was a real draft: sending it makes exactly what was typed, and opens its page.
        await dialog.get_by_role("button", name="Create").click()
        heading = page.locator("#item-title")
        await expect(heading).to_contain_text(f"Typed in a dialog {word}", timeout=SLOW)
        code = re.match(r"([EST]-\d+) ", await heading.inner_text())
        assert code is not None
        async with connect(project.mcp_url, project.key) as client:
            node = await ok(client, "get_node", node=ref_from_code(code[1]))
        assert node["title"] == f"Typed in a dialog {word}" and node["kind"] == "story"
        assert node["body"] == "Two lines\nin the details" and node["priority"] == "high"
        assert node["parent"] == epic and str(node["start_on"])[:10] == "2027-01-05"
        assert w.problems == [], w.problems
        assert await policy_violations(page) == []


# -- project isolation while the appearance changes ------------------------------------------------


async def test_switching_appearance_while_another_projects_answers_are_held_back_disturbs_nothing(
    base: str,
) -> None:
    word = unique_word()
    owner = register(base, name=f"Appearance {word}")
    first = make_project(owner, f"First {word}")
    second = make_project(owner, f"Second {word}")
    async with (
        connect(first.mcp_url, first.key) as one,
        connect(second.mcp_url, second.key) as two,
    ):
        epic_one = await made(one, "epic", f"Only in the first {word}")
        story_one = await made(one, "story", f"First story {word}", epic_one)
        await make_note(one, f"A first note {word}", nodes=[epic_one])
        await made(two, "epic", f"Only in the second {word}")

        async with themed_page(first.address, signed_in=True, keep_bodies=True) as w:
            page = w.page
            await in_project(page, f"{first.address}#/search?search=first+{word}")
            await expect(page.locator("#search-results tr.result").first).to_be_visible(
                timeout=SLOW
            )
            await w.go(f"#/item?item={story_one}&from=search")
            await expect(page.locator("#item-title")).to_contain_text(
                f"{code_of(story_one)} ", timeout=SLOW
            )
            # The first project's answers are held back, and an agent makes news for it.
            released = asyncio.Event()
            held: list[str] = []

            async def hold(route: Route) -> None:
                held.append(route.request.url)
                await released.wait()
                with contextlib.suppress(Exception):
                    await route.continue_()

            await page.route(f"**/board/api/projects/{first.id}/tool/**", hold)
            await made(one, "epic", f"News for the first {word}")
            for _ in range(200):
                if held:
                    break
                await asyncio.sleep(0.1)
            assert held, "no poll of the first project was held back"

            # Changing appearance while they are held back asks for nothing and shows nothing.
            before = len(w.requests)
            for choice in ("dark", "light", "dark"):
                await choose(page, choice)
                await settled(page, choice, choice)
            assert set(api_requests(w.requests[before:])) <= POLL, w.requests[before:]
            assert f"News for the first {word}" not in await page.locator("body").inner_text()

            # The other project is a new page, in the same appearance.
            await page.locator("#project-switcher").select_option(label=f"Second {word}")
            await page.wait_for_url(re.compile(rf"/board/p/{second.id}(#.*)?$"), timeout=SLOW)
            await page.wait_for_selector("#app[data-shell=project]", timeout=SLOW)
            await settled(page, "dark", "dark")
            region = page.get_by_role("region", name=f"Epic Only in the second {word}")
            await expect(region).to_be_visible(timeout=SLOW)
            after_switch = len(w.requests)
            await choose(page, "light")
            released.set()
            await asyncio.sleep(2)
            await choose(page, "dark")
            await settled(page, "dark", "dark")
            await asyncio.sleep(2)

            # Nothing of the first project, however late its answers were, and the page asked
            # only about the second since it came to it.
            shown = await page.locator("body").inner_text()
            for gone in (
                f"Only in the first {word}",
                f"News for the first {word}",
                f"First story {word}",
                f"A first note {word}",
            ):
                assert gone not in shown, gone
            assert not [url for url in w.requests[after_switch:] if f"/projects/{first.id}/" in url]
            for url, text in w.bodies:
                if f"/projects/{second.id}/" in url:
                    assert f"Only in the first {word}" not in text
                    assert f"News for the first {word}" not in text
            assert await policy_violations(page) == []


# -- every surface is themed, nothing is inverted --------------------------------------------------

BOARD = {
    "body": "body",
    "sidebar": "#sidebar",
    "card": ".board .card",
    "input": "#filter",
    "select": "#epic-filter",
    "primary": "#new-item",
}
FIELD = {"background": "--surface", "color": "--text", "border": "--border-strong"}
ACCENT = {"background": "--accent", "color": "--on-accent", "border": "--accent"}
PAGE = {"background": "--bg", "color": "--text"}
EXPECTED = {
    "body": PAGE,
    "sidebar": {"background": "--sidebar"},
    "card": {"background": "--surface", "border": "--border"},
    "input": FIELD,
    "select": FIELD,
    "primary": ACCENT,
}


def check_tokens(found: dict[str, Any], theme: str, expected: dict[str, dict[str, str]]) -> None:
    """Each named element is painted with the tokens of `theme` that its place in the design
    names, and the native control or scrollbar inside it says it is in that theme."""
    for name, wanted in expected.items():
        painting = found[name]
        assert painting is not None, f"{name} is not on the page"
        for part, colour in wanted.items():
            assert rgb_of(painting[part]) == token(theme, colour), (name, part, theme, painting)
        assert painting["scheme"] == theme, (name, painting)


FILTERS = """() => [
  'html', 'body', '#app', '#main-column', '#sidebar', '#page',
].map((selector) => [selector, document.querySelector(selector)
  && getComputedStyle(document.querySelector(selector)).filter])"""


async def painted_in_both(page: Page, selectors: dict[str, str]) -> Painted:
    """The colours of the named elements in Light and in Dark. In each theme every colour on the
    page comes from that theme's tokens, and no filter turns the page over mechanically."""
    found: Painted = {}
    for theme in THEMES:
        await switch(page, theme)
        found[theme] = await painted(page, selectors)
        assert foreign_colours(await survey(page), theme) == [], theme
        filters = await page.evaluate(FILTERS)
        assert all(value in (None, "none") for _, value in filters), filters
    return found


def differ(found: Painted, names: list[str], part: str) -> None:
    for name in names:
        light, dark = found["light"][name], found["dark"][name]
        assert light is not None and dark is not None, name
        assert light[part] != dark[part], (name, part, light[part], dark[part])


def of(found: Painted, theme: str, name: str) -> dict[str, str]:
    painting = found[theme][name]
    assert painting is not None, f"{name} is not on the page"
    return painting


async def test_the_board_the_controls_and_the_pages_of_search_and_activity_use_the_tokens(
    project: Project,
) -> None:
    async with connect(project.mcp_url, project.key) as client:
        await rich_world(client, project)
    async with themed_page(project.address, signed_in=True, scheme="light") as w:
        page = w.page
        await in_project(page, f"{project.address}#/board")
        await expect(page.locator(".board .card").first).to_be_visible(timeout=SLOW)
        found = await painted_in_both(page, BOARD)
        for theme in THEMES:
            check_tokens(found[theme], theme, EXPECTED)
        for name in ("body", "sidebar", "card", "input", "select"):
            differ(found, [name], "background")
        differ(found, ["body", "input"], "color")
        differ(found, ["primary", "input", "card"], "border")
        differ(found, ["primary"], "background")
        for theme in THEMES:
            # Text is darker than its page in Light and lighter in Dark.
            ink, ground = (
                rgb_of(of(found, theme, "body")["color"]),
                rgb_of(of(found, theme, "body")["background"]),
            )
            assert (luminance(ink) < luminance(ground)) == (theme == "light"), theme
            # The accent is vermilion in both (red before green before blue); a mechanical
            # inversion would have turned it blue-green.
            red, green, blue = rgb_of(of(found, theme, "primary")["background"])
            assert red > green > blue, (theme, red, green, blue)
        # Native controls and scrollbars are told which theme they are in.
        for theme in THEMES:
            await switch(page, theme)
            schemes = await page.evaluate(
                """() => [...document.querySelectorAll('input, select, textarea, button, dialog')]
                    .map((e) => getComputedStyle(e).colorScheme)"""
            )
            assert set(schemes) == {theme}, (theme, set(schemes))
            html = of({theme: await painted(page, {"html": "html"})}, theme, "html")
            assert rgb_of(html["scrollbar"].split(") ")[0] + ")") == token(theme, "--border-strong")

        # Search is a page: its form, its filters and its table of results are painted from the
        # tokens too, and so is Possible duplicates. The page has no button of its own: Enter asks.
        await w.go("#/search?search=log")
        await expect(page.locator("#search-results tr.result").first).to_be_visible(timeout=SLOW)
        search = await painted_in_both(
            page,
            {
                "input": "#search-box",
                "facets": "#search-facets",
                "head": "table.results thead th",
                "row": "table.results tbody tr.result td",
                "tab": "#search-tab-results",
            },
        )
        for theme in THEMES:
            check_tokens(
                search[theme],
                theme,
                {
                    "input": FIELD,
                    "facets": {"background": "--surface", "border": "--border"},
                    "head": {"background": "--surface-2"},
                    "row": {"color": "--text"},
                },
            )
        differ(search, ["input", "facets", "head"], "background")
        await w.go("#/search?dups=1")
        await expect(page.locator("#dup-count")).to_be_visible(timeout=SLOW)
        await painted_in_both(page, {"body": "body", "count": "#dup-count"})

        # Activity is a page with three views (Changes, Working, Free to take): the feed, who is
        # working on what, and the filters (behind the Filter button, opened as a person opens
        # them).
        await w.go("#/activity")
        await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
        await page.locator("#activity-tab-working").click()
        await expect(page.locator("#agents li.claim").first).to_be_visible(timeout=SLOW)
        working = await painted_in_both(
            page, {"body": "body", "working": "#working", "claim": "#agents li.claim"}
        )
        for theme in THEMES:
            check_tokens(working[theme], theme, {"body": PAGE})
            assert of(working, theme, "claim")["color"], theme
        differ(working, ["body"], "background")
        await page.locator("#activity-tab-changes").click()
        await expect(page.locator("#feed li.event").first).to_be_visible(timeout=SLOW)
        await page.locator("#activity-filter-toggle").click()
        await expect(page.locator("#activity-filter")).to_be_visible()
        activity = await painted_in_both(
            page,
            {
                "body": "body",
                "filters": "#activity-filter",
                "field": "#a-actor",
                "kind": "#a-kind",
                "apply": "#apply-activity",
                "event": "#feed li.event",
            },
        )
        for theme in THEMES:
            check_tokens(
                activity[theme],
                theme,
                {
                    "body": PAGE,
                    "filters": {"background": "--surface", "border": "--border"},
                    "field": FIELD,
                    "kind": FIELD,
                    "apply": FIELD,
                },
            )
        differ(activity, ["body", "filters", "field", "apply"], "background")
        assert w.problems == [], w.problems


async def test_the_page_of_an_item_its_table_and_its_form_use_the_tokens_of_each_theme(
    project: Project,
) -> None:
    async with connect(project.mcp_url, project.key) as client:
        world = await rich_world(client, project)
    story, task = world["story1"]["ref"], world["task1"]["ref"]
    async with themed_page(project.address, signed_in=True, scheme="light") as w:
        page = w.page
        # The page of a story, with its tasks in a table and its form for the details open.
        await in_project(page, f"{project.address}{item_address(story)}")
        await expect(page.locator("#item-table tbody tr").first).to_be_visible(timeout=SLOW)
        await expect(page.locator("#item-details .card-main")).to_be_visible(timeout=SLOW)
        await page.wait_for_load_state("networkidle")
        assert await page.locator("#panel, #drawer").count() == 0
        await open_editor(page, "#item-page")
        found = await painted_in_both(
            page,
            {
                "body": "body",
                "details": "#item-details .card-main",
                "pager": ".item-pager",
                "summary": ".card-summary",
                "head": "#item-table thead th",
                "sort": "#item-table th:not([aria-sort]) button.sort",
                "sorted": "#item-table th[aria-sort] button.sort",
                "row": "#item-table tbody tr td.cell-title",
                "title": "#item-title",
                "field": "#item-details #edit-title",
                "select": "#item-details #edit-status",
                "textarea": "#item-details #edit-body",
                "date": "#item-details #edit-start",
                "primary": "#item-details form.edit button.primary",
            },
        )
        for theme in THEMES:
            check_tokens(
                found[theme],
                theme,
                {
                    "body": PAGE,
                    "details": {"background": "--surface", "border": "--border"},
                    "pager": {"background": "--surface", "border": "--border-strong"},
                    "summary": {"background": "--surface", "border": "--border"},
                    "head": {"background": "--surface-2"},
                    "sort": {"color": "--text-2"},
                    "sorted": {"color": "--text"},
                    "title": {"color": "--text"},
                    "field": FIELD,
                    "select": FIELD,
                    "textarea": FIELD,
                    "date": FIELD,
                    "primary": ACCENT,
                },
            )
        differ(found, ["body", "details", "head", "field", "primary", "pager"], "background")
        differ(found, ["sort", "sorted", "title"], "color")
        # An epic, a task, an acceptance criterion, a decision and a plain note are themed the same.
        for ref in (
            world["epic"]["ref"],
            task,
            ref_of("rule", world["rule1"]["id"]),
            ref_of("note", world["decision2"]["id"]),
            ref_of("note", world["fact1"]["id"]),
        ):
            await in_project(page, f"{project.address}{item_address(ref)}")
            await expect(page.locator("#item-page")).to_have_attribute("data-ref", ref)
            await page.wait_for_load_state("networkidle")
            await painted_in_both(page, {"body": "body", "details": "#item-details"})
        assert w.problems == [], w.problems
        assert await policy_violations(page) == []


async def test_the_graph_in_every_tab_and_with_a_great_deal_of_work_uses_the_tokens_of_each_theme(
    project: Project,
) -> None:
    async with connect(project.mcp_url, project.key) as client:
        world = await rich_world(client, project)
        story = world["story1"]["ref"]
        # Enough work that the Structure map has a great many small things to draw.
        for number in range(70):
            await made(client, "task", f"Bulk task {number}", story)
    async with themed_page(project.address, signed_in=True, scheme="light") as w:
        page = w.page
        await in_project(page, f"{project.address}#/board")
        for address, drawn in (
            ("#/graph", "#graph-st-title"),
            (f"#/graph?tab=around&focus={story}", "#graph-hub"),
            ("#/graph?tab=order", ".graph-xv"),
            ("#/lists?view=everything", "#work-tree li"),
        ):
            await w.go(address)
            await expect(page.locator(drawn).first).to_be_visible(timeout=SLOW)
            if address == "#/graph":
                await rested(page)
            await page.wait_for_load_state("networkidle")
            await transitions_done(page)
            for theme in THEMES:
                await switch(page, theme)
                assert foreign_colours(await survey(page), theme) == [], (address, theme)
        # The Agent view, a dialog on the page of an item, is drawn from the tokens too.
        await w.go(item_address(story, "graph"))
        await expect(page.locator("#item-page")).to_have_attribute("data-ref", story, timeout=SLOW)
        await page.wait_for_load_state("networkidle")
        for theme in THEMES:
            await switch(page, theme)
            await page.locator("#item-agent-view").click()
            await expect(page.locator("#agent-view-text")).to_be_visible(timeout=SLOW)
            await transitions_done(page)
            assert foreign_colours(await survey(page), theme) == [], ("agent view", theme)
            await page.locator("#agent-view-close").click()
            await expect(page.locator("#agent-view")).to_have_count(0)
        # With this much work the Structure map draws every task as a thing of its own.
        await w.go("#/graph")
        await expect(page.locator("#graph-st-title")).to_be_visible(timeout=SLOW)
        assert await page.locator("#graph-view svg [data-ref^='task:']").count() > 60
        assert w.problems == [], w.problems


async def test_a_refusal_the_graph_the_roadmap_the_menu_and_the_other_pages_use_the_tokens(
    project: Project,
) -> None:
    async with connect(project.mcp_url, project.key) as client:
        world = await rich_world(client, project)
    story = world["story1"]["ref"]
    async with themed_page(project.address, signed_in=True, scheme="dark") as w:
        page = w.page

        # A refusal from the server, on the page of a record that is not there.
        await in_project(page, f"{project.address}#/item?item=story:99999")
        await expect(page.locator("#item-page .notice.refusal")).to_be_visible(timeout=SLOW)
        found = await painted_in_both(page, {"notice": "#item-page .notice.refusal"})
        for theme in THEMES:
            check_tokens(
                found[theme],
                theme,
                {
                    "notice": {
                        "background": "--danger-soft",
                        "border": "--danger",
                        "color": "--text",
                    }
                },
            )
        differ(found, ["notice"], "background")

        # The Graph: the item in the middle, the boxes around it and the words in them.
        await in_project(page, f"{project.address}#/graph?tab=around&focus={story}")
        await expect(page.locator("#graph-hub")).to_be_visible(timeout=SLOW)
        await expect(page.locator(".graph-gbox").first).to_be_attached(timeout=SLOW)
        await settled_boxes(page)
        graph = await painted_in_both(page, {"hub": "#graph-hub", "box": ".graph-gbox"})
        for theme in THEMES:
            assert rgb_of(of(graph, theme, "hub")["color"]) in palette(theme)
            assert rgb_of(of(graph, theme, "box")["color"]) in palette(theme)
        differ(graph, ["hub", "box"], "color")

        # The Lists, then the Roadmap: bars and today's line.
        await in_project(page, f"{project.address}#/lists?view=notes")
        await expect(page.locator("#ctx-notes")).to_be_visible(timeout=SLOW)
        await painted_in_both(page, {"body": "body"})
        await in_project(page, f"{project.address}#/roadmap")
        await expect(page.locator(".rm-bar").first).to_be_visible(timeout=SLOW)
        roadmap = await painted_in_both(page, {"bar": ".rm-bar", "today": ".rm-today"})
        for theme in THEMES:
            assert rgb_of(of(roadmap, theme, "bar")["border"]) in palette(theme)
            today = rgb_of(of(roadmap, theme, "today")["background"])
            assert today == token(theme, "--accent-soft")
        differ(roadmap, ["bar"], "color")
        differ(roadmap, ["today"], "background")

        # The other pages: nothing on them is painted from outside the tokens.
        for address in (
            "#/activity",
            "#/search?dups=1",
            "#/coverage",
            "#/users",
            "#/tools",
            "#/status",
            "#/account",
        ):
            await w.go(address)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(300)
            await painted_in_both(page, {"body": "body"})
        # The Settings menu and the account menu, which float over the page.
        await w.go("#/board")
        await page.locator("#settings-menu").click()
        await expect(page.locator("#settings-list")).to_be_visible()
        menu = await painted_in_both(page, {"menu": "#settings-list"})
        for theme in THEMES:
            assert rgb_of(of(menu, theme, "menu")["background"]) == token(theme, "--surface")
        differ(menu, ["menu"], "background")
        await open_account_menu(page)
        menu = await painted_in_both(
            page, {"menu": "#account-list", "checked": "#account-list [aria-checked=true]"}
        )
        for theme in THEMES:
            check_tokens(
                menu[theme], theme, {"menu": {"background": "--surface", "color": "--text"}}
            )
            assert of(menu, theme, "checked")["color"]
        differ(menu, ["menu"], "background")
        assert w.problems == [], w.problems


async def test_records_coverage_the_switcher_dialogs_and_a_new_key_use_the_tokens_of_each_theme(
    project: Project,
) -> None:
    async with connect(project.mcp_url, project.key) as client:
        world = await rich_world(client, project)
    story = world["story1"]["ref"]
    async with themed_page(project.address, signed_in=True, scheme="light") as w:
        page = w.page
        # The coverage report is a page, and a note, a decision and a criterion have pages.
        await in_project(page, f"{project.address}#/coverage")
        await expect(page.locator("#coverage-page")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#coverage-summary")).to_be_visible(timeout=SLOW)
        await page.wait_for_load_state("networkidle")
        coverage = await painted_in_both(
            page, {"page": "#coverage-page", "table": "#coverage-page table"}
        )
        for theme in THEMES:
            check_tokens(coverage[theme], theme, {"page": {"color": "--text"}})
        for kind, record in (
            ("note", world["fact1"]),
            ("note", world["decision2"]),
            ("rule", world["rule1"]),
        ):
            await w.go(item_address(ref_of(kind, record["id"])))
            await expect(page.locator("#item-details .record-detail")).to_be_visible(timeout=SLOW)
            found = await painted_in_both(
                page, {"page": "#item-page", "details": "#item-details .record-detail"}
            )
            for theme in THEMES:
                check_tokens(found[theme], theme, {"page": {"color": "--text"}})

        # The dialog that adds a note, which can name who decided it, and the ones that replace and
        # withdraw one.
        await w.go(item_address(story))
        await expect(page.locator("#item-title")).to_contain_text(
            f"{code_of(story)} ", timeout=SLOW
        )
        await page.locator("#item-details button.add-note").first.click()
        await expect(page.locator("#add-note-text")).to_be_visible()
        found = await painted_in_both(page, {"dialog": "dialog.modal", "field": "#add-note-text"})
        for theme in THEMES:
            assert rgb_of(of(found, theme, "dialog")["background"]) == token(theme, "--surface")
        await page.keyboard.press("Escape")
        await expect(page.locator("dialog.modal")).to_have_count(0)
        await w.go(item_address(ref_of("note", world["fact1"]["id"])))
        for button in ("button.supersede", "button.withdraw"):
            await expect(page.locator(f"#item-page {button}")).to_be_visible(timeout=SLOW)
            await page.locator(f"#item-page {button}").first.click()
            await expect(page.locator("dialog.modal")).to_be_visible()
            await painted_in_both(page, {"dialog": "dialog.modal"})
            await page.keyboard.press("Escape")
            await expect(page.locator("dialog.modal")).to_have_count(0)

        # The quick switcher, which floats over everything.
        await w.go("#/board")
        await page.keyboard.press("Control+k")
        await expect(page.locator("#switcher")).to_be_visible()
        await page.locator("#switcher-input").fill("log")
        await expect(page.locator("#switcher-list li").first).to_be_visible()
        found = await painted_in_both(page, {"switcher": "#switcher", "input": "#switcher-input"})
        for theme in THEMES:
            assert rgb_of(of(found, theme, "switcher")["background"]) == token(theme, "--surface")
        await page.keyboard.press("Escape")

        # A new agent key: its dialog, and then the secret, shown once.
        await w.go("#/users")
        await page.locator("#key-open").click()
        await expect(page.locator("#key-name")).to_be_visible(timeout=SLOW)
        dialog = await painted_in_both(page, {"dialog": "dialog.modal", "field": "#key-name"})
        for theme in THEMES:
            assert rgb_of(of(dialog, theme, "dialog")["background"]) == token(theme, "--surface")
        differ(dialog, ["dialog"], "background")
        await page.locator("#key-name").fill("Surveyed")
        await page.locator("#key-call-tools").check()
        await page.locator("#key-tools-choice-standard").click()
        await expect(page.locator("#create-key")).to_be_enabled(timeout=SLOW)
        await page.locator("#create-key").click()
        await expect(page.locator("#key-secret")).to_be_visible(timeout=SLOW)
        found = await painted_in_both(page, {"secret": "#key-secret", "reveal": "#new-key"})
        for theme in THEMES:
            assert of(found, theme, "secret")["color"], theme
        differ(found, ["reveal"], "background")
        assert w.problems == [], w.problems
        assert await policy_violations(page) == []


async def test_the_dialog_the_project_list_and_the_sign_in_page_use_the_tokens_of_each_theme(
    base: str, project: Project
) -> None:
    async with themed_page(project.address, signed_in=True, scheme="light") as w:
        page = w.page
        await in_project(page, f"{project.address}#/board")
        await page.locator("#new-item").click()
        await expect(page.locator("dialog.modal")).to_be_visible()
        found = await painted_in_both(
            page,
            {
                "dialog": "dialog.modal",
                "field": "dialog.modal #new-title",
                "select": "dialog.modal #new-kind",
                "primary": "dialog.modal button.primary",
            },
        )
        for theme in THEMES:
            check_tokens(
                found[theme],
                theme,
                {
                    "dialog": {"background": "--surface", "color": "--text", "border": "--border"},
                    "field": {"background": "--surface", "border": "--border-strong"},
                    "select": {"background": "--surface", "border": "--border-strong"},
                    "primary": {"background": "--accent", "color": "--on-accent"},
                },
            )
            backdrop = of(found, theme, "dialog")["backdrop"]
            assert parse_colour(backdrop or "") == colour_tokens(theme)["--scrim"]
        differ(found, ["dialog", "field", "primary"], "background")

        # The person's project list, with the control in the top bar.
        await project_list(page, f"{base}/board")
        listed = await painted_in_both(
            page,
            {
                "body": "body",
                "input": "#project-name",
                "primary": "#create-project",
                "appearance": "#top .appearance",
            },
        )
        for theme in THEMES:
            check_tokens(
                listed[theme],
                theme,
                {
                    "body": PAGE,
                    "input": {"background": "--surface", "border": "--border-strong"},
                    "primary": ACCENT,
                    "appearance": {"background": "--surface", "border": "--border-strong"},
                },
            )
        differ(listed, ["body", "input", "primary"], "background")
        assert w.problems == [], w.problems

    # The sign-in page, before anybody is signed in, in a browser of its own.
    async with themed_page(f"{base}/board", signed_in=False, scheme="light", name=None) as w:
        page = w.page
        await entry(page, f"{base}/board")
        signed_out = await painted_in_both(
            page,
            {
                "body": "body",
                "input": "#account-email",
                "primary": "#sign-in-submit",
                "appearance": "#top .appearance",
                "link": ".entry-switch .link",
            },
        )
        for theme in THEMES:
            check_tokens(
                signed_out[theme],
                theme,
                {
                    "body": PAGE,
                    "input": {"background": "--surface", "border": "--border-strong"},
                    "primary": ACCENT,
                    "appearance": {"background": "--surface", "border": "--border-strong"},
                },
            )
            assert rgb_of(of(signed_out, theme, "link")["color"]) == token(theme, "--accent-text")
        differ(signed_out, ["body", "input", "primary"], "background")
        differ(signed_out, ["link"], "color")
        assert w.problems == [], w.problems


async def test_the_chips_and_the_meter_keep_their_meaning_in_both_themes(project: Project) -> None:
    async with connect(project.mcp_url, project.key) as client:
        world = await rich_world(client, project)
    async with themed_page(project.address, signed_in=True, scheme="light") as w:
        page = w.page
        await in_project(page, f"{project.address}{item_address(world['epic']['ref'])}")
        await expect(page.locator("#item-page .meter").first).to_be_visible(timeout=SLOW)
        pairs = (("ok", "--ok-soft"), ("warn", "--warn-soft"), ("danger", "--danger-soft"))
        for theme in THEMES:
            await switch(page, theme)
            colours = await page.evaluate(
                """() => {
                    const of = (selector) => {
                        const el = document.querySelector(selector);
                        if (!el) return null;
                        const style = getComputedStyle(el);
                        return [style.color, style.backgroundColor];
                    };
                    return {
                        ok: of('.chip.ok, .chip.confirmed'),
                        warn: of('.chip.warn, .chip.unconfirmed'),
                        danger: of('.chip.danger'),
                        meter: of('#item-page .meter > span'),
                        track: of('#item-page .meter'),
                    };
                }"""
            )
            # A status keeps its hue in both themes, as the text and tint tokens of that status,
            # and the bar of a meter is the token for done on its track.
            for name, soft in pairs:
                if colours[name] is not None:
                    text, tint = (rgb_of(each) for each in colours[name])
                    assert (text, tint) == (token(theme, f"--{name}"), token(theme, soft)), name
            assert rgb_of(colours["meter"][1]) == token(theme, "--ok")
            assert rgb_of(colours["track"][1]) == token(theme, "--surface-3")
        assert w.problems == [], w.problems
