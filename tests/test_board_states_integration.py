"""M17, proof 5: the states a person meets when the board is waiting, empty, refused, out of date,
without its embedding service or cut off from the server, in a real headless browser against the
real containers, in Light and in Dark.

Each state is a designed one (words, a sign as well as a colour, the theme's tokens), the server's
own refusals are shown in the server's own words, what was typed is never lost, and the board shows
the data that was made meanwhile once it recovers. Nothing leaves the server, and nothing a person
writes is ever drawn as markup."""

import asyncio
import re

import pytest
from playwright.async_api import Locator, Page, Route, expect

from tests.board_helpers import SLOW, hold_back_news, made, plain
from tests.board_theme import (
    THEMES,
    choose,
    contrast,
    entry,
    foreign_colours,
    hold_answers,
    in_project,
    painted,
    pixels,
    policy_violations,
    project_list,
    refusal_look,
    rgb_of,
    settled,
    survey,
    switch,
    themed_page,
    token,
    transitions_done,
)
from tests.helpers import (
    connect,
    embeddings_stopped,
    make_fact,
    ok,
    refused,
    reindexed,
    unique_word,
)
from tests.world import Project, api, make_project, register
from vrum.board import STATIC

pytestmark = [pytest.mark.integration, pytest.mark.anyio]
SCHEMES = pytest.mark.parametrize("scheme", THEMES)
BROKEN = ("undefined", "null", "NaN", "[object", "Error executing")


@pytest.fixture
def base(server_url: str) -> str:
    return server_url.removesuffix("/mcp")


@pytest.fixture
def project(base: str) -> Project:
    return make_project(register(base))


def words_of(mcp: str) -> str:
    """What the board shows of a refusal the server sent a client: the same words, without the
    protocol's prefix."""
    return mcp.split(": ", 1)[1].strip() if mcp.startswith("Error executing tool") else mcp.strip()


async def says(page: Page, selector: str, *words: str) -> str:
    """The text of something on the page, which is there, has all of `words` in it (waited for) and
    is words, not a stray `undefined`."""
    place = page.locator(selector).first
    await expect(place).to_be_visible(timeout=SLOW)
    for word in words:
        await expect(place).to_contain_text(word, timeout=SLOW)
    text = " ".join((await place.inner_text()).split())
    for broken in BROKEN:
        assert broken not in text, (selector, text)
    return text


async def refusal_is_designed(place: Locator, theme: str, *, sign: bool = True) -> None:
    """A refusal reads at AA against what is behind it, says what it is for (it is an alert), is
    in the theme's danger colour and, where it can be, has a sign beside its words."""
    look = await refusal_look(place)
    assert look["role"] == "alert" and look["words"], look
    assert look["icon"] or not sign, look
    ink, ground = rgb_of(look["color"]), rgb_of(look["background"])
    assert contrast(ink, ground) >= 4.5, look
    assert ink == token(theme, "--danger"), (theme, look)


async def themed(page: Page, theme: str) -> None:
    """Nothing on the page is painted from outside the tokens of the theme it is in."""
    await transitions_done(page)
    assert foreign_colours(await survey(page), theme) == [], theme


# -- loading ---------------------------------------------------------------------------------------


@SCHEMES
async def test_while_the_project_loads_the_page_says_so_and_is_never_blank(
    project: Project, scheme: str
) -> None:
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Loading epic {word}")
        story = await made(client, "story", f"Loading story {word}", epic)
    async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
        page = w.page
        held = await hold_answers(page, project, "list_nodes")
        await page.goto(project.address)
        await held.arrived()
        loading = page.locator("#page .loading")
        await expect(loading).to_be_visible()
        await expect(loading).to_have_text("Loading…")
        assert await loading.get_attribute("role") == "status"
        await expect(page.locator("#app")).to_have_attribute("data-shell", "loading")
        await expect(page.locator("#sidebar")).to_be_hidden()
        # Not blank, in words and in the picture: the box has its own colours in the theme, and a
        # line across it is not one flat colour.
        found = await painted(page, {"loading": "#page .loading", "body": "body"})
        assert found["loading"] is not None
        assert rgb_of(found["loading"]["color"]) == token(scheme, "--text-2")
        assert rgb_of(found["loading"]["border"]) == token(scheme, "--border-strong")
        assert rgb_of(found["body"]["background"]) == token(scheme, "--bg")  # type: ignore[index]
        box = await loading.bounding_box()
        assert box is not None and box["width"] > 100 and box["height"] > 20
        shot = await page.screenshot(type="png")
        line = [
            (int(box["x"] + box["width"] * step / 80), int(box["y"] + box["height"] / 2))
            for step in range(1, 80)
        ]
        assert len(set(await pixels(page, shot, line))) >= 2
        await themed(page, scheme)
        held.release.set()
        await expect(page.locator("#app")).to_have_attribute("data-shell", "project", timeout=SLOW)
        await expect(page.get_by_role("region", name=f"Epic Loading epic {word}")).to_be_visible(
            timeout=SLOW
        )
        await expect(page.locator("#page .loading")).to_have_count(0)

        # The detail panel says so while it reads one item, and the search drawer while it searches.
        panel = await hold_answers(page, project, "get_node")
        await w.go(f"#/board?sel=node:{story}")
        await panel.arrived()
        await expect(page.locator("#panel .loading")).to_have_text("Loading…")
        await expect(page.locator("#panel")).to_be_visible()
        await themed(page, scheme)
        panel.release.set()
        await expect(page.locator("#card-title")).to_contain_text(f"#{story} ", timeout=SLOW)
        asking = await hold_answers(page, project, "search")
        await w.go(f"#/board?sel=node:{story}&drawer=search&search={word}")
        await asking.arrived()
        await expect(page.locator("#search-results .loading")).to_have_text("Searching…")
        await themed(page, scheme)
        asking.release.set()
        await expect(page.locator("#search-results li.result").first).to_be_visible(timeout=SLOW)

        # Reading a filter (which the server reads) says so on the Board and on the Roadmap, when
        # a page is opened with one.
        reading = await hold_answers(page, project, "list_nodes", when=lambda text: "query" in text)
        await w.go("#/roadmap")
        await expect(page.locator("#rm-scroll")).to_be_visible(timeout=SLOW)
        await w.go("#/board?q=kind:story")
        await reading.arrived()
        await expect(page.locator("#board-loading")).to_have_text("Reading the filter…")
        await themed(page, scheme)
        await w.go("#/roadmap?q=kind:story")
        await expect(page.locator("#rm-scroll .loading")).to_have_text("Reading the timeline…")
        await themed(page, scheme)
        reading.release.set()
        await expect(page.locator(".rm-bar, .tray-item").first).to_be_visible(timeout=SLOW)
        assert w.problems == [], w.problems
        assert await policy_violations(page) == []


@SCHEMES
async def test_the_graph_says_it_is_loading_while_the_context_is_read(
    project: Project, scheme: str
) -> None:
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        await made(client, "epic", f"Graph epic {word}")
    async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
        page = w.page
        held = await hold_answers(page, project, "list_facts")
        await page.goto(f"{project.address}#/graph")
        await held.arrived()
        await expect(page.locator("#graph-loading")).to_have_text("Loading the graph")
        await themed(page, scheme)
        held.release.set()
        await expect(page.locator("#graph-svg[data-ready=true]")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#graph-loading")).to_be_hidden()
        assert w.problems == [], w.problems


# -- empty -----------------------------------------------------------------------------------------


@SCHEMES
async def test_a_new_project_shows_friendly_empty_states_with_a_next_step(
    project: Project, scheme: str
) -> None:
    async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
        page = w.page
        await in_project(page, f"{project.address}#/board")
        text = await says(page, "#board-empty", "Nothing here yet", "Create an epic")
        assert len(text) > 20
        await says(page, ".side-epics", "No epics yet")
        await expect(page.locator("#new-item")).to_be_visible()
        await themed(page, scheme)

        # The Graph: the map, and the list in each of its two views, say why and what to do.
        await w.go("#/graph")
        await says(page, "#graph-empty", "No work in this project yet", "New item")
        await themed(page, scheme)
        await page.locator("#graph-empty-action").click()
        await expect(page.locator("dialog.modal")).to_be_visible()
        await page.locator("dialog.modal").get_by_role("button", name="Cancel").click()
        await expect(page.locator("dialog.modal")).to_have_count(0)
        await w.go("#/graph?view=context")
        await says(page, "#graph-empty", "No context in this project yet", "Open the list")
        await w.go("#/graph?as=list")
        await says(page, ".gv-empty", "No work in this project yet", "New item")
        await themed(page, scheme)
        await w.go("#/graph?view=context&as=list")
        await says(page, "#ctx-requirements", "There are no requirements here yet")
        await says(page, "#ctx-decisions", "There are no decisions here yet")
        await says(page, "#ctx-facts", "There are no facts here yet")
        await themed(page, scheme)

        # The Roadmap, the search drawer (nothing asked, then nothing found), Activity, the
        # possible duplicates and a filter that matches nothing.
        await w.go("#/roadmap")
        await says(page, ".rm-drop-hint", "Nothing to show", "Make some work on the Board")
        await themed(page, scheme)
        await w.go("#/board?drawer=search")
        await says(page, "#search-results", "Type something to look for")
        await w.go("#/board?drawer=search&search=nothing+matches+this")
        await says(page, "#search-results", "Nothing matches", "Try other words")
        await says(page, "#search-count", "0 results")
        await themed(page, scheme)
        await w.go("#/board?drawer=search&dups=1")
        await says(page, "#dup-pairs", "Nothing to compare at this level")
        await w.go("#/board?drawer=activity")
        await says(page, "#working", "Nobody holds anything right now", "Nobody has taken work yet")
        await themed(page, scheme)
        await w.go("#/board?q=status:done")
        await says(page, "#board-empty", "Nothing matches this filter", "clear the filter")
        await themed(page, scheme)
        assert w.problems == [], w.problems
        assert await policy_violations(page) == []


@SCHEMES
async def test_a_search_with_no_results_in_a_project_with_work_says_so_and_lets_the_filters_go(
    project: Project, scheme: str
) -> None:
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Something real {word}")
        await made(client, "story", f"A story {word}", epic)
        await reindexed(client)
    async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
        page = w.page
        await in_project(page, f"{project.address}#/board?drawer=search&search={word}")
        await expect(page.locator("#search-results li.result")).to_have_count(2, timeout=SLOW)
        # Filters that match none of the results: a message and a way back, not a blank drawer.
        await w.go(f"#/board?drawer=search&search={word}&search_type=rule")
        empty = page.locator("#search-results .empty")
        await expect(empty).to_contain_text("Nothing matches these filters", timeout=SLOW)
        await empty.get_by_role("button", name="Show all 2 results").click()
        await expect(page.locator("#search-results li.result")).to_have_count(2, timeout=SLOW)
        await w.go("#/board?drawer=search&search=qqqqqqqq")
        await says(page, "#search-results", "Nothing matches")
        await themed(page, scheme)
        assert w.problems == [], w.problems


# -- invalid input ---------------------------------------------------------------------------------


@SCHEMES
async def test_a_blank_title_and_bad_dates_are_refused_in_the_servers_words_and_the_draft_stays(
    project: Project, scheme: str
) -> None:
    word = unique_word()
    async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
        page = w.page
        await in_project(page, f"{project.address}#/board")
        await page.locator("#new-item").click()
        dialog = page.locator("dialog.modal")
        await expect(dialog).to_be_visible()
        async with connect(project.mcp_url, project.key) as client:
            # A blank title: the server says no, in its words, under the fields.
            mcp = await refused(
                client, "create_node", actor="Tester", kind="epic", title="   ", priority="high"
            )
            await dialog.locator("#new-body").fill("The details are kept")
            await dialog.locator("#new-priority").select_option("high")
            await dialog.locator("#new-title").fill("   ")
            await dialog.get_by_role("button", name="Create").click()
            problem = dialog.locator(".problem")
            words = problem.locator(".problem-text")
            await expect(words).to_have_text(words_of(mcp), timeout=SLOW)
            plain(await words.inner_text(), mcp)
            await refusal_is_designed(problem, scheme)
            await themed(page, scheme)
            assert await dialog.locator("#new-body").input_value() == "The details are kept"
            assert await dialog.locator("#new-priority").input_value() == "high"
            assert await dialog.locator("#new-title").input_value() == "   "
            await expect(dialog.get_by_role("button", name="Create")).to_be_enabled()

            # A start after the target: refused in the server's words, the whole draft stays.
            mcp = await refused(
                client,
                "create_node",
                actor="Tester",
                kind="epic",
                title=f"Dated {word}",
                body="The details are kept",
                priority="high",
                start_on="2027-05-10",
                target_on="2027-05-01",
            )
            await dialog.locator("#new-title").fill(f"Dated {word}")
            await dialog.locator("#new-start").fill("2027-05-10")
            await dialog.locator("#new-target").fill("2027-05-01")
            await dialog.get_by_role("button", name="Create").click()
            await expect(words).to_have_text(words_of(mcp), timeout=SLOW)
            plain(await words.inner_text(), mcp)
            await refusal_is_designed(problem, scheme)
            assert await dialog.locator("#new-title").input_value() == f"Dated {word}"
            assert await dialog.locator("#new-start").input_value() == "2027-05-10"
            assert await dialog.locator("#new-target").input_value() == "2027-05-01"
            assert await dialog.locator("#new-body").input_value() == "The details are kept"
            assert (await ok(client, "list_nodes", query=word))["items"] == []

            # Corrected in the same dialog, the same draft is sent and made.
            await dialog.locator("#new-target").fill("2027-06-01")
            await dialog.get_by_role("button", name="Create").click()
            heading = page.locator("#card-title")
            await expect(heading).to_contain_text(f"Dated {word}", timeout=SLOW)
            found = re.match(r"#(\d+) ", await heading.inner_text())
            assert found is not None
            node = await ok(client, "get_node", node_id=int(found.group(1)))
            assert node["body"] == "The details are kept" and node["priority"] == "high"
            assert str(node["start_on"])[:10] == "2027-05-10"

            # The same in the panel's form for an item that is there: refused, draft stays.
            mcp = await refused(
                client,
                "update_node",
                actor="Tester",
                node_id=node["id"],
                expected_version=node["version"],
                start_on="2027-09-10",
                target_on="2027-09-01",
                body="A new body that is only a draft",
            )
            await page.locator("#edit-start").fill("2027-09-10")
            await page.locator("#edit-target").fill("2027-09-01")
            await page.locator("#edit-body").fill("A new body that is only a draft")
            await page.get_by_role("button", name="Save changes").click()
            panel_problem = page.locator("#panel .edit .problem")
            panel_words = panel_problem.locator(".problem-text")
            await expect(panel_words).to_have_text(words_of(mcp), timeout=SLOW)
            plain(await panel_words.inner_text(), mcp)
            await refusal_is_designed(panel_problem, scheme)
            assert await page.locator("#edit-start").input_value() == "2027-09-10"
            assert await page.locator("#edit-target").input_value() == "2027-09-01"
            assert (
                await page.locator("#edit-body").input_value() == "A new body that is only a draft"
            )
            assert (await ok(client, "get_node", node_id=node["id"]))["version"] == node["version"]
        assert w.problems == [], w.problems
        assert await policy_violations(page) == []


@SCHEMES
async def test_bad_filter_text_is_refused_in_the_servers_words_on_every_view_and_stays_typed(
    project: Project, scheme: str
) -> None:
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Filtered {word}")
        await made(client, "story", f"Filter story {word}", epic)
        async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
            page = w.page
            await in_project(page, f"{project.address}#/board")
            for view in ("board", "graph", "roadmap"):
                await w.go(f"#/{view}")
                await expect(page.locator("#filter")).to_be_visible(timeout=SLOW)
                for bad in ("status:bogus", "target:2026-13-45", 'kind:"story', "colour:red"):
                    mcp = await refused(client, "list_nodes", query=bad)
                    await page.locator("#filter").fill(bad)
                    await page.locator("form.filter button[type=submit]").click()
                    problem = page.locator("#filter-problem")
                    await expect(problem).to_have_text(words_of(mcp), timeout=SLOW)
                    plain((await problem.inner_text()).strip(), mcp)
                    assert await page.locator("#filter").input_value() == bad, (view, bad)
                    assert await problem.get_attribute("role") == "alert"
                    await refusal_is_designed(problem, scheme, sign=False)
                    await themed(page, scheme)
                    # The page is still the page: not blank, not a broken view.
                    await expect(page.locator("main#page h1").first).to_be_visible()
                await page.locator("#clear-filter").click()
                await expect(page.locator("#filter-problem")).to_be_empty(timeout=SLOW)
                assert await page.locator("#filter").input_value() == ""
            assert w.problems == [], w.problems


@SCHEMES
async def test_the_sign_in_and_project_forms_show_the_servers_refusals_and_keep_what_was_typed(
    base: str, scheme: str
) -> None:
    owner = register(base)
    async with themed_page(f"{base}/board", signed_in=False, scheme=scheme, name=None) as w:
        page = w.page
        await entry(page, f"{base}/board")
        problem = page.locator("#entry-problem")

        async def server_says(path: str, body: dict[str, str]) -> str:
            reply = api(base, "POST", path, body)
            assert reply.status >= 400 and reply.body["message"]
            return str(reply.body["message"])

        async def refused_as(path: str, body: dict[str, str]) -> None:
            """The page shows exactly what the server says to the same request."""
            expected = await server_says(path, body)
            await expect(problem.locator(".problem-text")).to_have_text(expected, timeout=SLOW)
            await refusal_is_designed(problem, scheme)

        # No password, then a wrong one: the server's words, and what was typed stays typed.
        nobody = f"nobody-{unique_word()}@example.test"
        await page.locator("#account-email").fill(nobody)
        await page.locator("#sign-in-submit").click()
        await refused_as("/board/api/session", {"email": nobody, "password": ""})
        await page.locator("#account-email").fill(owner.email)
        await page.locator("#account-password").fill("not the password")
        await page.locator("#sign-in-submit").click()
        await refused_as(
            "/board/api/session", {"email": owner.email, "password": "not the password"}
        )
        assert await page.locator("#account-email").input_value() == owner.email
        assert await page.locator("#account-password").input_value() == "not the password"
        await themed(page, scheme)

        # Registering with a password that is too short, and then with an address that is taken.
        await page.locator("#show-register").click()
        await page.locator("#account-name").fill("A New Person")
        await page.locator("#account-email").fill(owner.email)
        await page.locator("#account-password").fill("short")
        await page.locator("#register-submit").click()
        short = {"email": owner.email, "name": "A New Person", "password": "short"}
        await refused_as("/board/api/accounts", short)
        assert await page.locator("#account-name").input_value() == "A New Person"
        assert await page.locator("#account-email").input_value() == owner.email
        await themed(page, scheme)
        await page.locator("#account-password").fill("a long enough password")
        await page.locator("#register-submit").click()
        taken = {**short, "password": "a long enough password"}
        await refused_as("/board/api/accounts", taken)
        assert await page.locator("#account-password").input_value() == "a long enough password"

        # A project without a name, once signed in.
        await page.locator("#show-sign-in").click()
        await page.locator("#account-email").fill(owner.email)
        await page.locator("#account-password").fill(owner.password)
        await page.locator("#sign-in-submit").click()
        await page.wait_for_selector("#projects, #no-projects", timeout=SLOW)
        await page.locator("#project-name").fill("   ")
        await page.locator("#create-project").click()
        shown = page.locator("#project-problem")
        reply = owner.call("POST", "/board/api/projects", {"name": "   "})
        assert reply.status == 400
        await expect(shown.locator(".problem-text")).to_have_text(
            reply.body["message"], timeout=SLOW
        )
        await refusal_is_designed(shown, scheme)
        assert await page.locator("#project-name").input_value() == "   "
        await themed(page, scheme)
        # The browser logs a refused request as a failed load; only those are expected here.
        assert [p for p in w.problems if not re.search(r"status of (401|409)", p)] == []


@SCHEMES
async def test_bad_dates_in_the_roadmap_tray_are_refused_in_the_servers_words_and_stay_typed(
    project: Project, scheme: str
) -> None:
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Roadmap epic {word}")
        story = await made(client, "story", f"Undated story {word}", epic)
        node = await ok(client, "get_node", node_id=story)
        mcp = await refused(
            client,
            "update_node",
            actor="Tester",
            node_id=story,
            expected_version=node["version"],
            start_on="2027-02-10",
            target_on="2027-02-01",
        )
        async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
            page = w.page
            await in_project(page, f"{project.address}#/roadmap")
            await expect(page.locator(f".tray-item[data-id='{story}']")).to_have_count(1)
            item = page.locator(f".tray-item[data-id='{story}']")
            await expect(item).to_be_visible(timeout=SLOW)

            # Nothing typed: the board's own words, as a refusal in the notices.
            await item.get_by_role("button", name="Set dates").click()
            notice = page.locator("#notices .notice.refusal").first
            await expect(notice).to_contain_text("Give a start date, a target date or both")
            await notice.get_by_role("button", name="Dismiss").click()

            # A start after the target: the server says no, in its words, and the dates stay.
            await item.get_by_label(f"Start date of #{story}").fill("2027-02-10")
            await item.get_by_label(f"Target date of #{story}").fill("2027-02-01")
            await item.get_by_role("button", name="Set dates").click()
            notice = page.locator("#notices .notice.refusal").first
            await expect(notice.locator(".notice-text")).to_have_text(words_of(mcp), timeout=SLOW)
            plain(await notice.locator(".notice-text").inner_text(), mcp)
            assert await notice.get_attribute("role") == "alert"
            assert await notice.locator("svg").count() >= 1
            assert "Not done." in await notice.inner_text()
            found = await painted(page, {"notice": "#notices .notice.refusal"})
            assert found["notice"] is not None
            assert rgb_of(found["notice"]["background"]) == token(scheme, "--danger-soft")
            assert rgb_of(found["notice"]["border"]) == token(scheme, "--danger")
            await themed(page, scheme)
            # The tray was drawn again after the refusal, and still holds what was typed.
            item = page.locator(f".tray-item[data-id='{story}']")
            await expect(item.get_by_label(f"Start date of #{story}")).to_have_value("2027-02-10")
            await expect(item.get_by_label(f"Target date of #{story}")).to_have_value("2027-02-01")
            assert (await ok(client, "get_node", node_id=story))["version"] == node["version"]

            # Corrected, the same two fields make the dates.
            await item.get_by_label(f"Target date of #{story}").fill("2027-02-20")
            await item.get_by_role("button", name="Set dates").click()
            await expect(page.locator(f".rm-bar[data-id='{story}']")).to_be_visible(timeout=SLOW)
            saved = await ok(client, "get_node", node_id=story)
            assert str(saved["start_on"])[:10] == "2027-02-10"
            assert str(saved["target_on"])[:10] == "2027-02-20"


# -- stale version ---------------------------------------------------------------------------------


@SCHEMES
async def test_saving_over_an_agents_change_is_refused_in_the_servers_words_and_keeps_the_draft(
    project: Project, scheme: str
) -> None:
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Epic {word}")
        story = await made(client, "story", f"Original title {word}", epic, body="Original words")
        async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
            page = w.page
            await in_project(page, f"{project.address}#/board?sel=node:{story}")
            await expect(page.locator("#card-title")).to_contain_text(f"#{story} ", timeout=SLOW)
            # The board has not heard of what the agent does next, as in the seconds before it does.
            await hold_back_news(page)
            await page.locator("#edit-title").fill("My own title")
            await page.locator("#edit-body").fill("My own words, not saved yet")
            await ok(
                client,
                "update_node",
                actor="agent-1",
                node_id=story,
                expected_version=1,
                title=f"Agent title {word}",
                body="The agent's words",
            )
            await page.get_by_role("button", name="Save changes").click()
            problem = page.locator("#panel .edit .problem")
            words = problem.locator(".problem-text")
            await expect(words).to_be_visible(timeout=SLOW)
            plain(
                (await words.inner_text()).strip(),
                await refused(
                    client,
                    "update_node",
                    actor="Tester",
                    node_id=story,
                    expected_version=1,
                    title="My own title",
                    body="My own words, not saved yet",
                ),
            )
            await refusal_is_designed(problem, scheme)
            assert "refusal-text" in (await words.get_attribute("class") or "")
            await themed(page, scheme)
            # What was typed is still there, and nothing was saved over the agent's change.
            assert await page.locator("#edit-title").input_value() == "My own title"
            assert await page.locator("#edit-body").input_value() == "My own words, not saved yet"
            saved = await ok(client, "get_node", node_id=story)
            assert saved["version"] == 2 and saved["title"] == f"Agent title {word}"

            # The refusal offers the current version; choosing it shows the fresh data.
            load = problem.get_by_role(
                "button", name=re.compile(r"^Load the current version \(2\)")
            )
            await expect(load).to_be_visible()
            await load.click()
            await expect(page.locator("#edit-title")).to_have_value(f"Agent title {word}")
            await expect(page.locator("#card-title")).to_contain_text(f"Agent title {word}")
            assert await page.locator("#edit-body").input_value() == "The agent's words"
            await expect(page.locator("#panel .edit .problem .problem-text")).to_have_count(0)
            # With the current version in front of them the person can save, and it works.
            await page.locator("#edit-title").fill(f"Both agreed {word}")
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator("#notices .notice.done")).to_contain_text(
                f"Saved story #{story}", timeout=SLOW
            )
            saved = await ok(client, "get_node", node_id=story)
            assert saved["version"] == 3 and saved["title"] == f"Both agreed {word}"
            assert w.problems == [], w.problems


@SCHEMES
async def test_an_agents_change_while_typing_is_offered_not_forced_and_loading_it_shows_fresh_data(
    project: Project, scheme: str
) -> None:
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Epic {word}")
        story = await made(client, "story", f"Typing title {word}", epic)
        async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
            page = w.page
            await in_project(page, f"{project.address}#/board?sel=node:{story}")
            await expect(page.locator("#card-title")).to_contain_text(f"#{story} ", timeout=SLOW)
            await page.locator("#edit-title").fill("What I am typing")
            await ok(
                client,
                "update_node",
                actor="agent-7",
                node_id=story,
                expected_version=1,
                title=f"Changed by the agent {word}",
            )
            # The board hears of it within its poll, and offers it; it does not take over.
            banner = page.locator("#panel .notice.changed")
            await expect(banner).to_be_visible(timeout=SLOW)
            text = " ".join((await banner.inner_text()).split())
            assert "This item changed (agent-7) while you were typing" in text, text
            assert "what you typed is still here" in text
            assert await banner.get_attribute("role") == "status"
            assert await page.locator("#edit-title").input_value() == "What I am typing"
            await themed(page, scheme)
            await banner.get_by_role("button", name="Load the current version").click()
            await expect(page.locator("#edit-title")).to_have_value(f"Changed by the agent {word}")
            await expect(page.locator("#card-title")).to_contain_text(
                f"Changed by the agent {word}"
            )
            await expect(page.locator("#panel .notice.changed")).to_have_count(0)
            assert w.problems == [], w.problems


# -- unavailable embeddings ------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["dark"])
async def test_without_embeddings_search_still_finds_exact_words_says_so_and_then_recovers(
    project: Project, scheme: str
) -> None:
    word, later = unique_word(), unique_word()
    paraphrase = "Clients get a bill by mail"
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Billing {word}")
        story = await made(
            client, "story", f"Send the invoice to the customer by email {word}", epic
        )
        await reindexed(client)
        async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
            page = w.page
            await in_project(page, f"{project.address}#/board?sel=node:{story}&drawer=search")

            async def search(text: str, mode: str = "both") -> None:
                await page.locator("#search-box").fill(text)
                await page.locator("#search-mode").select_option(mode)
                await page.locator("#search-go").click()

            # With the service, a paraphrase is found by meaning, and the note says it was used.
            await search(paraphrase)
            await expect(
                page.locator(f"#search-results li.result[data-id='{story}'][data-matched=meaning]")
            ).to_have_count(1, timeout=SLOW)
            await expect(page.locator("#search-note")).to_contain_text("Meaning was used")
            await expect(page.locator("#search-note")).to_have_class(re.compile(r"\binfo\b"))

            with embeddings_stopped():
                # A story made now waits to be indexed; its words find it the moment it is saved.
                late = await made(client, "story", f"Made while the service was down {later}", epic)
                await search(later)
                rows = page.locator("#search-results li.result")
                await expect(rows).to_have_count(1, timeout=SLOW)
                assert await rows.first.get_attribute("data-id") == str(late)
                assert await rows.first.get_attribute("data-matched") == "words"
                # The drawer says plainly, in the server's words, that meaning was not used.
                note = page.locator("#search-note")
                await expect(note).to_contain_text("cannot be reached", timeout=SLOW)
                await expect(note).to_contain_text("Only exact words were searched")
                mcp = await ok(client, "search", query=later, mode="both")
                assert (await note.inner_text()).strip() == mcp["index"]["note"]
                assert mcp["index"]["meaning"] == "service_unreachable"
                await expect(note).to_have_class(re.compile(r"\bwarn\b"))
                assert await note.locator("svg").count() == 1
                await expect(page.locator("#search-waiting")).to_contain_text(
                    "waiting to be indexed"
                )
                await expect(page.locator("#index-state")).to_contain_text("waiting")
                # By meaning alone it cannot be done, which the drawer says in the server's words.
                await search(paraphrase, "meaning")
                problem = page.locator("#search-problem")
                await expect(problem).to_contain_text(
                    "Searching by meaning is not possible now", timeout=SLOW
                )
                plain(
                    (await problem.inner_text()).strip(),
                    await refused(client, "search", query=paraphrase, mode="meaning"),
                )
                await refusal_is_designed(problem, scheme, sign=False)
                assert await page.locator("#search-results li.result").count() == 0
                # The panel of the story that is not indexed yet says it too, in the server's words
                # (this story's meaning cannot be worked out until the service is back).
                similar = await refused(
                    client, "similar_items", node_id=late, types=["story"], limit=5
                )
                await w.go(f"#/board?sel=node:{late}&drawer=search&search={later}")
                suggestion = page.locator("#panel [data-part=overlaps]")
                await expect(suggestion).to_contain_text("Might be the same as", timeout=SLOW)
                await expect(suggestion).to_contain_text(words_of(similar), timeout=SLOW)
                # The notes and refusals are painted from the tokens of both themes.
                for theme in THEMES:
                    await switch(page, theme)
                    await themed(page, theme)
                await switch(page, scheme)
                # Exact words go on working after a second ask, and nothing was written.
                await search(later, "words")
                await expect(page.locator("#search-results li.result")).to_have_count(
                    1, timeout=SLOW
                )

            # The service is back and the index is brought up to date: meaning works again.
            await reindexed(client)
            await search(paraphrase)
            await expect(page.locator("#search-note")).to_contain_text(
                "Meaning was used", timeout=SLOW
            )
            await expect(page.locator("#search-note")).to_have_class(re.compile(r"\binfo\b"))
            await expect(
                page.locator(f"#search-results li.result[data-id='{story}'][data-matched=meaning]")
            ).to_have_count(1, timeout=SLOW)
            await expect(page.locator("#search-problem")).to_have_text("")
            await search(paraphrase, "meaning")
            await expect(page.locator("#search-results li.result").first).to_be_visible(
                timeout=SLOW
            )
            await expect(page.locator("#search-problem")).to_have_text("")
            assert w.problems == [], w.problems
            assert await policy_violations(page) == []


# -- disconnected and reconnected ------------------------------------------------------------------


async def lost(route: Route) -> None:
    await route.abort("connectionrefused")


@SCHEMES
async def test_losing_the_server_says_so_in_the_current_view_keeps_the_draft_and_recovers_with_news(
    project: Project, scheme: str
) -> None:
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Epic {word}")
        story = await made(client, "story", f"Before the loss {word}", epic)
        async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
            page = w.page
            await in_project(page, f"{project.address}#/board?sel=node:{story}")
            await expect(page.locator("#card-title")).to_contain_text(f"#{story} ", timeout=SLOW)
            await page.locator("#edit-title").fill("Typed while the server was away")
            note, chip = page.locator("#connection-note"), page.locator("#connection")
            await expect(note).to_be_hidden()
            await expect(chip).to_be_hidden()

            # The server cannot be reached: the board says so in the view that is open, with a
            # sign as well as a colour, and keeps what it has.
            await page.route("**/board/api/**", lost)
            await expect(note).to_be_visible(timeout=20_000)
            await expect(chip).to_be_visible()
            words = " ".join((await note.inner_text()).split())
            assert "cannot reach the server" in words and "trying again" in words, words
            look = await refusal_look(note)
            assert look["icon"] and look["role"] == "alert"
            assert contrast(rgb_of(look["color"]), rgb_of(look["background"])) >= 4.5, look
            assert rgb_of(look["background"]) == token(scheme, "--warn-soft")
            await expect(chip).to_contain_text("Cannot reach the server")
            assert await chip.locator("svg").count() == 1
            await themed(page, scheme)
            for view in ("graph", "roadmap", "board"):
                await w.go(f"#/{view}?sel=node:{story}")
                await expect(page.locator("main#page h1").first).to_be_visible()
                await expect(note).to_be_visible()
                await expect(chip).to_be_visible()
                await expect(page.locator("#panel")).to_be_visible()
                await themed(page, scheme)
            assert (
                await page.locator("#edit-title").input_value() == "Typed while the server was away"
            )
            # Saving says why it cannot be done, and the draft is still there.
            await page.get_by_role("button", name="Save changes").click()
            problem = page.locator("#panel .edit .problem")
            await expect(problem.locator(".problem-text")).to_have_text(
                "The board cannot reach the server. Check that it is running, then try again.",
                timeout=SLOW,
            )
            await refusal_is_designed(problem, scheme)
            assert (
                await page.locator("#edit-title").input_value() == "Typed while the server was away"
            )

            # Meanwhile an agent works.
            news = await made(client, "epic", f"Made while the board was cut off {word}")
            await ok(
                client,
                "update_node",
                actor="agent-9",
                node_id=story,
                expected_version=1,
                title=f"Renamed during the loss {word}",
            )

            # The server is back: the notice and the chip go, the board shows what was made
            # meanwhile, the draft stays and the agent's change to the item is offered.
            await page.unroute("**/board/api/**", lost)
            await expect(note).to_be_hidden(timeout=20_000)
            await expect(chip).to_be_hidden()
            await expect(
                page.get_by_role("region", name=f"Epic Made while the board was cut off {word}")
            ).to_be_visible(timeout=SLOW)
            await expect(page.locator(f"#epic-list a[data-epic='{news}']")).to_be_visible()
            assert (
                await page.locator("#edit-title").input_value() == "Typed while the server was away"
            )
            banner = page.locator("#panel .notice.changed")
            await expect(banner).to_contain_text("This item changed (agent-9)", timeout=SLOW)
            await banner.get_by_role("button", name="Load the current version").click()
            await expect(page.locator("#edit-title")).to_have_value(
                f"Renamed during the loss {word}"
            )
            await expect(page.locator("#card-title")).to_contain_text(
                f"Renamed during the loss {word}"
            )
            await themed(page, scheme)


@SCHEMES
async def test_a_board_that_cannot_start_says_so_offers_to_try_again_and_starts_when_it_can(
    project: Project, scheme: str
) -> None:
    async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
        page = w.page
        pattern = f"**/board/api/projects/{project.id}/tool/**"
        await page.route(pattern, lost)
        await page.goto(project.address)
        failure = page.locator("#board-unavailable")
        await expect(failure).to_be_visible(timeout=SLOW)
        assert await failure.get_attribute("role") == "alert"
        text = await says(
            page, "#board-unavailable", "The board could not start", "cannot reach the server"
        )
        assert "Try again" in text
        await expect(page.locator("#retry")).to_be_visible()
        assert await page.locator("#sidebar").is_hidden()
        await themed(page, scheme)
        found = await painted(page, {"box": "#board-unavailable"})
        assert found["box"] is not None and rgb_of(found["box"]["border"]) == token(
            scheme, "--border-strong"
        )
        # Even now the appearance can be changed, from the top bar, and both themes are painted
        # from the tokens.
        await choose(page, "dark" if scheme == "light" else "light")
        await settled(
            page, "dark" if scheme == "light" else "light", "dark" if scheme == "light" else "light"
        )
        await themed(page, "dark" if scheme == "light" else "light")
        await choose(page, scheme)
        await page.unroute(pattern, lost)
        await page.locator("#retry").click()
        await expect(page.locator("#app")).to_have_attribute("data-shell", "project", timeout=SLOW)
        await expect(page.locator("#board-unavailable")).to_have_count(0)


@SCHEMES
async def test_a_project_that_is_not_the_persons_says_so_with_the_appearance_control_in_the_tokens(
    base: str, project: Project, scheme: str
) -> None:
    stranger = make_project(register(base), "Not yours")
    async with themed_page(project.address, signed_in=True, scheme=scheme, name=None) as w:
        page = w.page
        await page.goto(stranger.address)
        await page.wait_for_selector("#no-such-project", timeout=SLOW)
        words = await says(page, "#no-such-project")
        assert "Not yours" not in words and stranger.name not in words
        await expect(page.get_by_role("link", name="Back to your projects")).to_be_visible()
        other = "dark" if scheme == "light" else "light"
        await choose(page, other)
        await settled(page, other, other)
        await themed(page, other)
        await choose(page, scheme)
        await themed(page, scheme)


# -- nothing leaves the server, nothing is drawn as markup -----------------------------------------


async def test_no_request_leaves_the_server_and_what_people_write_is_only_ever_text(
    base: str, project: Project
) -> None:
    word = unique_word()
    hostile = '<img src="http://evil.example/x.png" onerror="window.__pwned=1"><b>bold</b>'
    body = (
        f"![tracker](http://evil.example/pixel.png)\n\n[click](javascript:window.__pwned=2)\n\n"
        f"<script>window.__pwned=3</script><style>body{{display:none}}</style> {word}"
    )
    async with connect(project.mcp_url, project.key) as client:
        epic = await made(client, "epic", f"Epic {hostile} {word}", body=body)
        story = await made(client, "story", f"Story {word}", epic, body=body)
        await made(
            client, "task", f"Task {word}", story, start_on="2026-12-01", target_on="2026-12-09"
        )
        await ok(client, "retain_fact", actor="a", text=f"{hostile} {word}", source=hostile)
        await reindexed(client)
    async with themed_page(project.address, signed_in=True, scheme="light") as w:
        page = w.page
        await in_project(page, f"{project.address}#/board")
        for address in (
            "#/board",
            f"#/board?sel=node:{epic}",
            f"#/board?sel=node:{story}&drawer=search&search={word}",
            "#/board?drawer=activity",
            "#/board?drawer=search&dups=1",
            "#/graph",
            "#/graph?view=everything&as=list",
            "#/roadmap",
            "#/access",
            "#/tools",
            "#/status",
        ):
            await w.go(address)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(300)
            for theme in THEMES:
                await switch(page, theme)
        # What was written is shown as the words it is, never as the markup it spells.
        await w.go(f"#/board?sel=node:{epic}")
        await expect(page.locator("#card-title")).to_contain_text("<img src=", timeout=SLOW)
        await expect(page.locator("#panel .body-text")).to_contain_text("<script>window.__pwned=3")
        assert await page.locator("img, #panel b, script:not([src]), style").count() == 0
        assert await page.evaluate("window.__pwned") is None
        # A link written with javascript: in it is not one that does anything.
        for link in await page.locator("#panel .body-text a").all():
            assert not (await link.get_attribute("href") or "").lower().startswith("javascript")
        await w.go("#/board")
        await page.locator("#new-item").click()
        await expect(page.locator("dialog.modal")).to_be_visible()
        await page.keyboard.press("Escape")
        assert await policy_violations(page) == []

        # The person's projects and the sign-in page load nothing from elsewhere either.
        await project_list(page, f"{base}/board")
        await page.locator("#sign-out").click()
        await page.wait_for_selector("#sign-in-form", timeout=SLOW)
        for theme in THEMES:
            await switch(page, theme)

        # Every request of the whole browser went to the server it came from.
        outside = [url for url in w.requests if not url.startswith(f"{base}/")]
        assert outside == [], outside
        assert not [url for url in w.requests if "evil.example" in url]
        assert w.dialogs == [] and w.problems == [], (w.dialogs, w.problems)


async def test_the_page_files_ask_for_no_font_script_image_or_style_from_anywhere_else(
    base: str, project: Project
) -> None:
    # In the browser no font face was declared or loaded, and every resource the page used is the
    # server's own. (That the files name nothing outside is checked in test_board.py.)
    async with themed_page(project.address, signed_in=True, scheme="dark") as w:
        page = w.page
        await in_project(page, f"{project.address}#/board")
        await page.wait_for_load_state("networkidle")
        faces = await page.evaluate("[...document.fonts].map((face) => face.family)")
        assert faces == []
        names = await page.evaluate("performance.getEntriesByType('resource').map((e) => e.name)")
        assert names and all(name.startswith(f"{base}/") for name in names), names
        css = "".join(
            path.read_text(encoding="utf-8").lower()
            for path in STATIC.glob("*.css")
            if path.name != "tokens.css"
        )
        assert "@font-face" not in css and "@import" not in css
        assert not re.search(r"url\(\s*['\"]?(?:https?:)?//", css)
        assert w.problems == [], w.problems


# -- project switching rejects late answers --------------------------------------------------------


@SCHEMES
async def test_late_answers_of_the_project_left_never_reach_the_one_entered_even_with_the_same_ids(
    base: str, scheme: str
) -> None:
    word = unique_word()
    owner = register(base, name=f"Late {word}")
    first = make_project(owner, f"First {word}")
    second = make_project(owner, f"Second {word}")
    async with (
        connect(first.mcp_url, first.key) as one,
        connect(second.mcp_url, second.key) as two,
    ):
        # Both projects number their items from 1: the same ids hold different things.
        epic_one = await made(one, "epic", f"Only in the first {word}")
        assert epic_one == await made(two, "epic", f"Only in the second {word}")
        fact_one = await make_fact(one, f"A first fact {word}", node_ids=[epic_one])
        fact_two = await make_fact(two, f"A second fact {word}", node_ids=[epic_one])
        assert fact_one["id"] == fact_two["id"]
        async with themed_page(first.address, signed_in=True, scheme=scheme, keep_bodies=True) as w:
            page = w.page
            await in_project(
                page,
                f"{first.address}#/board?sel=node:{epic_one}&drawer=search&search=first+{word}",
            )
            await expect(page.locator("#card-title")).to_contain_text(f"Only in the first {word}")
            # From now on the first project's answers are held back: the panel's, the search's
            # and the poll's. An agent makes news in it, and the person leaves for the second.
            held = [
                await hold_answers(page, first, tool)
                for tool in ("get_node", "search", "changes_since", "recall", "list_nodes")
            ]
            await made(one, "epic", f"News for the first {word}")
            await held[2].arrived()
            before = len(w.requests)
            await page.locator("#project-switcher").select_option(label=f"Second {word}")
            await page.wait_for_url(re.compile(rf"/board/p/{second.id}(#.*)?$"), timeout=SLOW)
            await page.wait_for_selector("#app[data-shell=project]", timeout=SLOW)
            await w.go(f"#/board?sel=node:{epic_one}&drawer=search&search={word}")
            await expect(page.locator("#card-title")).to_contain_text(
                f"Only in the second {word}", timeout=SLOW
            )
            for each in held:
                each.release.set()
            await asyncio.sleep(2)
            shown = await page.locator("body").inner_text()
            for gone in (
                f"Only in the first {word}",
                f"News for the first {word}",
                f"A first fact {word}",
            ):
                assert gone not in shown, gone
            await expect(page.locator("#card-title")).to_contain_text(f"Only in the second {word}")
            await switch(page, "dark" if scheme == "light" else "light")
            # The page asked only about the second project after it came to it, and no answer it
            # was given had a word of the first.
            assert not [url for url in w.requests[before:] if f"/projects/{first.id}/" in url]
            for url, text in w.bodies:
                if f"/projects/{second.id}/" in url:
                    assert f"Only in the first {word}" not in text
                    assert f"News for the first {word}" not in text
                    assert f"A first fact {word}" not in text
            assert await policy_violations(page) == []

            # And when the session ends under an open project (it is ended from elsewhere), the
            # answers still on their way are dropped: the sign-in page stays as it is, with
            # nothing of the project on it and no error.
            late = [
                await hold_answers(page, second, tool) for tool in ("get_node", "recall", "search")
            ]
            epic_two = await made(two, "epic", f"Made as the session ended {word}")
            await expect(
                page.get_by_role("region", name=f"Epic Made as the session ended {word}")
            ).to_be_visible(timeout=SLOW)
            await w.go(f"#/board?sel=node:{epic_two}&drawer=search&search=second+{word}")
            await late[0].arrived()
            await late[2].arrived()
            csrf = (await page.evaluate("fetch('/board/api/session').then((r) => r.json())"))[
                "csrf"
            ]
            ended = await page.evaluate(
                "(csrf) => fetch('/board/api/session', {method: 'DELETE', headers: "
                "{'X-Vrum-CSRF': csrf}}).then((r) => r.status)",
                csrf,
            )
            assert ended == 200
            await expect(page.locator(".entry")).to_be_visible(timeout=20_000)
            await expect(page.locator("#entry-problem")).to_contain_text(
                "Your session has ended", timeout=SLOW
            )
            for each in late:
                each.release.set()
            await asyncio.sleep(2)
            await expect(page.locator(".entry")).to_be_visible()
            await expect(page.locator("#panel")).to_be_hidden()
            await expect(page.locator("#drawer")).to_be_hidden()
            assert word not in await page.locator("body").inner_text()
            assert [problem for problem in w.problems if problem.startswith("page error")] == []
            await themed(page, "dark" if scheme == "light" else "light")
