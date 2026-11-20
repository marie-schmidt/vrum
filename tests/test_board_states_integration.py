"""M17, proof 5, as M20 has it: the states a person meets when the board is waiting, empty, refused,
out of date, without its embedding service or cut off from the server, in a real headless browser
against the real containers, in Light and in Dark. Every one of them is on a page (there is no panel
and no drawer): the record's own page, Search, Activity, the coverage report and the views.

Each state is a designed one (words, a sign as well as a colour, the theme's tokens), the server's
own refusals are shown in the server's own words, what was typed is never lost, and the board shows
the data that was made meanwhile once it recovers. Nothing leaves the server, and nothing a person
writes is ever drawn as markup."""

import asyncio
import re

import pytest
from playwright.async_api import Locator, Page, Route, expect

from tests.board_helpers import SLOW, hold_back_news, item_address, made, open_editor, plain
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
    code_of,
    connect,
    connect_as,
    embeddings_stopped,
    make_note,
    number_of,
    ok,
    ref_from_code,
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

        # A record's page says so while it reads the item, Search while it searches, and so do
        # Activity (the changes, and the claims) and the coverage report.
        reading_item = await hold_answers(page, project, "get_node")
        await w.go(item_address(story))
        await reading_item.arrived()
        await expect(page.locator("#item-details .loading")).to_have_text("Loading…")
        await expect(page.locator("#item-page")).to_be_visible()
        await themed(page, scheme)
        reading_item.release.set()
        await expect(page.locator("#item-title")).to_contain_text(
            f"{code_of(story)} ", timeout=SLOW
        )
        await expect(page.locator("#item-details .loading")).to_have_count(0, timeout=SLOW)
        asking = await hold_answers(page, project, "search")
        await w.go(f"#/search?search={word}")
        await asking.arrived()
        await expect(page.locator("#search-results .loading")).to_have_text("Searching…")
        await themed(page, scheme)
        asking.release.set()
        await expect(page.locator("#search-results tr.result").first).to_be_visible(timeout=SLOW)
        claims = await hold_answers(page, project, "who_is_working")
        await w.go("#/activity")
        await claims.arrived()
        await expect(page.locator("#working .loading")).to_have_text("Reading the claims…")
        await themed(page, scheme)
        claims.release.set()
        await expect(page.locator("#working .loading")).to_have_count(0, timeout=SLOW)
        covering = await hold_answers(page, project, "coverage_report")
        await w.go("#/coverage")
        await covering.arrived()
        await expect(page.locator("#coverage-page .loading")).to_have_text("Loading…")
        await themed(page, scheme)
        covering.release.set()
        await expect(page.locator("#coverage-summary")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#coverage-page .loading")).to_have_count(0)

        # Reading a filter (which the server reads) says so on the Board and on the Roadmap, when
        # a page is opened with one.
        reading = await hold_answers(page, project, "list_nodes", when=lambda text: "query" in text)
        await w.go("#/roadmap")
        await expect(page.locator("#rm-scroll")).to_be_visible(timeout=SLOW)
        await w.go("#/board?q=kind:story")
        await reading.arrived()
        await expect(page.locator("#board-loading")).to_have_text("Loading…")
        await themed(page, scheme)
        await w.go("#/roadmap?q=kind:story")
        await expect(page.locator("#rm-scroll .loading")).to_have_text("Reading the timeline…")
        await themed(page, scheme)
        reading.release.set()
        await expect(page.locator("#rm-scroll")).to_have_attribute(
            "data-ready", "true", timeout=SLOW
        )
        assert w.problems == [], w.problems
        assert await policy_violations(page) == []


@SCHEMES
async def test_the_graph_says_it_is_loading_while_the_notes_are_read(
    project: Project, scheme: str
) -> None:
    word = unique_word()
    async with connect(project.mcp_url, project.key) as client:
        await made(client, "epic", f"Graph epic {word}")
    async with themed_page(project.address, signed_in=True, scheme=scheme) as w:
        page = w.page
        held = await hold_answers(page, project, "list_notes")
        await page.goto(f"{project.address}#/graph")
        await held.arrived()
        await expect(page.locator("#graph-view .loading")).to_have_text("Loading the graph…")
        await themed(page, scheme)
        held.release.set()
        await expect(page.locator("#graph-st-title")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#graph-view .loading")).to_have_count(0)
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

        # The Graph, in each way of looking, says why it is empty and what to do; the Lists say it
        # for the work, the acceptance criteria and the notes.
        await w.go("#/graph")
        await says(page, "#graph-view", "Nothing is planned yet", "When the first epic is made")
        await themed(page, scheme)
        await page.locator("#graph-create").click()
        await expect(page.locator("dialog.modal")).to_be_visible()
        await page.locator("dialog.modal").get_by_role("button", name="Cancel").click()
        await expect(page.locator("dialog.modal")).to_have_count(0)
        await w.go("#/graph?tab=around")
        await says(page, "#graph-around-empty", "Choose something to look around")
        await w.go("#/graph?tab=order")
        await says(page, "#graph-view", "There is nothing to order yet", "some work first")
        # An address from the time of the Timeline tab opens Structure, without a word.
        await w.go("#/graph?tab=timeline")
        await says(page, "#graph-view", "Nothing is planned yet", "When the first epic is made")
        await themed(page, scheme)
        await w.go("#/lists")
        await says(page, ".gv-empty", "No work in this project yet", "New item")
        await themed(page, scheme)
        await w.go("#/lists?view=everything")
        await says(page, "#ctx-requirements", "None yet.")
        await says(page, "#ctx-notes", "None yet.")
        await themed(page, scheme)

        # The Roadmap, the Search page (nothing asked, then nothing found), Possible duplicates,
        # Activity, the coverage report and a filter that matches nothing.
        await w.go("#/roadmap")
        await says(page, "#rm-empty", "No work to show yet", "Make some on the Board")
        await themed(page, scheme)
        await w.go("#/search")
        await says(page, "#search-results", "Type something to look for")
        await w.go("#/search?search=nothing+matches+this")
        await says(page, "#search-results", "Nothing matches", "Try other words")
        await says(page, "#search-count", "0 results")
        await themed(page, scheme)
        await w.go("#/search?dups=1")
        await says(page, "#dup-pairs", "Nothing to compare at this level")
        await w.go("#/activity")
        await says(page, "#working", "No claims in force", "Nobody has taken work yet")
        await says(page, "#feed", "Nothing has happened in this project yet")
        await themed(page, scheme)
        await w.go("#/coverage")
        await expect(page.locator("#coverage-summary")).to_be_visible(timeout=SLOW)
        await says(
            page, "#coverage-page", "Every acceptance criterion is placed", "Every story has"
        )
        await themed(page, scheme)
        await w.go("#/item?item=story:99999")
        await says(page, "#item-page", "Nothing to show here", "Back to Board")
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
        await in_project(page, f"{project.address}#/search?search={word}")
        await expect(page.locator("#search-results tr.result")).to_have_count(2, timeout=SLOW)
        # Filters that match none of the results: a message and a way back, not a blank page.
        await w.go(f"#/search?search={word}&search_type=rule")
        empty = page.locator("#search-results .empty")
        await expect(empty).to_contain_text("Nothing matches these filters", timeout=SLOW)
        await empty.get_by_role("button", name="Show all 2 results").click()
        await expect(page.locator("#search-results tr.result")).to_have_count(2, timeout=SLOW)
        await w.go("#/search?search=qqqqqqqq")
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
            mcp = await refused(client, "create_node", kind="epic", title="   ", priority="high")
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
            heading = page.locator("#item-title")
            await expect(heading).to_contain_text(f"Dated {word}", timeout=SLOW)
            found = re.match(r"([EST]-\d+) ", await heading.inner_text())
            assert found is not None
            node = await ok(client, "get_node", node=ref_from_code(found.group(1)))
            assert node["body"] == "The details are kept" and node["priority"] == "high"
            assert str(node["start_on"])[:10] == "2027-05-10"

            # The same in the page's own form for an item that is there: refused, draft stays.
            mcp = await refused(
                client,
                "update_node",
                node=node["ref"],
                expected_version=node["version"],
                start_on="2027-09-10",
                target_on="2027-09-01",
                body="A new body that is only a draft",
            )
            await expect(page.locator("#item-page")).to_have_attribute(
                "data-ref", node["ref"], timeout=SLOW
            )
            await open_editor(page)
            await page.locator("#edit-start").fill("2027-09-10")
            await page.locator("#edit-target").fill("2027-09-01")
            await page.locator("#edit-body").fill("A new body that is only a draft")
            await page.get_by_role("button", name="Save changes").click()
            page_problem = page.locator("#item-details form.edit .problem")
            page_words = page_problem.locator(".problem-text")
            await expect(page_words).to_have_text(words_of(mcp), timeout=SLOW)
            plain(await page_words.inner_text(), mcp)
            await refusal_is_designed(page_problem, scheme)
            assert await page.locator("#edit-start").input_value() == "2027-09-10"
            assert await page.locator("#edit-target").input_value() == "2027-09-01"
            assert (
                await page.locator("#edit-body").input_value() == "A new body that is only a draft"
            )
            assert (await ok(client, "get_node", node=node["ref"]))["version"] == node["version"]
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
            for view in ("board", "lists", "roadmap"):
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
            await in_project(page, f"{project.address}{item_address(story)}")
            await expect(page.locator("#item-title")).to_contain_text(
                f"{code_of(story)} ", timeout=SLOW
            )
            # The board has not heard of what the agent does next, as in the seconds before it does.
            await hold_back_news(page)
            await open_editor(page)
            await page.locator("#edit-title").fill("My own title")
            await page.locator("#edit-body").fill("My own words, not saved yet")
            async with connect_as("agent-1", project) as by_agent:
                await ok(
                    by_agent,
                    "update_node",
                    node=story,
                    expected_version=1,
                    title=f"Agent title {word}",
                    body="The agent's words",
                )
            await page.get_by_role("button", name="Save changes").click()
            problem = page.locator("#item-details form.edit .problem")
            words = problem.locator(".problem-text")
            await expect(words).to_be_visible(timeout=SLOW)
            plain(
                (await words.inner_text()).strip(),
                await refused(
                    client,
                    "update_node",
                    node=story,
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
            saved = await ok(client, "get_node", node=story)
            assert saved["version"] == 2 and saved["title"] == f"Agent title {word}"

            # The refusal offers the current version; choosing it shows the fresh data.
            load = problem.get_by_role(
                "button", name=re.compile(r"^Load the current version \(2\)")
            )
            await expect(load).to_be_visible()
            await load.click()
            await expect(page.locator("#edit-title")).to_have_value(f"Agent title {word}")
            assert await page.locator("#edit-body").input_value() == "The agent's words"
            await expect(
                page.locator("#item-details form.edit .problem .problem-text")
            ).to_have_count(0)
            # With the current version in front of them the person can save, and it works.
            await page.locator("#edit-title").fill(f"Both agreed {word}")
            await page.get_by_role("button", name="Save changes").click()
            await expect(page.locator("#notices .notice.done")).to_contain_text(
                f"Saved story {number_of(story)}", timeout=SLOW
            )
            saved = await ok(client, "get_node", node=story)
            assert saved["version"] == 3 and saved["title"] == f"Both agreed {word}"
            # The board hears of the news again, and the title of the page follows (it is read from
            # what the board holds, which had been told nothing).
            await page.unroute("**/board/api/projects/*/tool/changes_since")
            await expect(page.locator("#item-title")).to_contain_text(
                f"Both agreed {word}", timeout=SLOW
            )
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
            await in_project(page, f"{project.address}{item_address(story)}")
            await expect(page.locator("#item-title")).to_contain_text(
                f"{code_of(story)} ", timeout=SLOW
            )
            await open_editor(page)
            await page.locator("#edit-title").fill("What I am typing")
            async with connect_as("agent-7", project) as by_agent:
                await ok(
                    by_agent,
                    "update_node",
                    node=story,
                    expected_version=1,
                    title=f"Changed by the agent {word}",
                )
            # The board hears of it within its poll, and offers it; it does not take over.
            banner = page.locator("#item-details .notice.changed")
            await expect(banner).to_be_visible(timeout=SLOW)
            text = " ".join((await banner.inner_text()).split())
            assert "This item changed (agent-7) while you were typing" in text, text
            assert "what you typed is still here" in text
            assert await banner.get_attribute("role") == "status"
            assert await page.locator("#edit-title").input_value() == "What I am typing"
            await themed(page, scheme)
            await banner.get_by_role("button", name="Load the current version").click()
            await expect(page.locator("#edit-title")).to_have_value(f"Changed by the agent {word}")
            await expect(page.locator("#item-title")).to_contain_text(
                f"Changed by the agent {word}"
            )
            await expect(page.locator("#item-details .notice.changed")).to_have_count(0)
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
            await in_project(page, f"{project.address}#/search")

            async def search(text: str) -> None:
                await page.locator("#search-box").fill(text)
                await page.locator("#search-go").click()

            # With the service, a paraphrase is found by meaning. The row says how it matched, and
            # the page has no note to add: it speaks only when meaning could not be used.
            await search(paraphrase)
            await expect(
                page.locator(f"#search-results tr.result[data-ref='{story}'][data-matched=meaning]")
            ).to_have_count(1, timeout=SLOW)
            await expect(page.locator("#search-note")).to_have_text("")
            await expect(page.locator("#search-note")).to_be_hidden()

            with embeddings_stopped():
                # A story made now waits to be indexed; its words find it the moment it is saved.
                late = await made(client, "story", f"Made while the service was down {later}", epic)
                await search(later)
                rows = page.locator("#search-results tr.result")
                await expect(rows).to_have_count(1, timeout=SLOW)
                assert await rows.first.get_attribute("data-ref") == late
                assert await rows.first.get_attribute("data-matched") == "words"
                # The page says plainly, in the server's words, that meaning was not used.
                note = page.locator("#search-note")
                await expect(note).to_contain_text("cannot be reached", timeout=SLOW)
                await expect(note).to_contain_text("Only exact words were searched")
                mcp = await ok(client, "search", query=later, mode="both")
                assert (await note.inner_text()).strip() == mcp["index"]["note"]
                assert mcp["index"]["meaning"] == "service_unreachable"
                assert await note.locator("svg").count() == 1
                await expect(page.locator("#search-waiting")).to_contain_text(
                    "waiting to be indexed"
                )
                await expect(page.locator("#index-state")).to_contain_text("waiting")
                # A question in other words is no refusal: the page lists what the exact words
                # found (the same count as the server's) and says again that meaning was not used.
                found = await ok(client, "search", query=paraphrase)
                await search(paraphrase)
                await expect(page.locator("#search-count")).to_have_text(
                    re.compile(rf"^{found['total']} results?\b"), timeout=SLOW
                )
                await expect(page.locator("#search-problem")).to_have_text("")
                await expect(note).to_contain_text("Only exact words were searched")
                assert (await note.inner_text()).strip() == found["index"]["note"]
                # The page of the story that is not indexed yet says it too, in the server's words
                # (this story's meaning cannot be worked out until the service is back).
                similar = await refused(
                    client, "similar_items", node=late, types=["story"], limit=5
                )
                await w.go(f"{item_address(late)}&from=search")
                suggestion = page.locator("#item-page [data-part=overlaps]")
                await expect(suggestion).to_contain_text("Might be the same as", timeout=SLOW)
                await expect(suggestion).to_contain_text(words_of(similar), timeout=SLOW)
                # The note is painted from the tokens of both themes.
                for theme in THEMES:
                    await switch(page, theme)
                    await themed(page, theme)
                await switch(page, scheme)
                # Exact words go on working after a second ask, and nothing was written.
                await w.go(f"#/search?search={later}")
                await expect(page.locator("#search-results tr.result")).to_have_count(
                    1, timeout=SLOW
                )

            # The service is back and the index is brought up to date: meaning works again, and the
            # warning goes away without anything taking its place.
            await reindexed(client)
            await search(paraphrase)
            await expect(page.locator("#search-note")).to_have_text("", timeout=SLOW)
            await expect(page.locator("#search-note")).to_be_hidden()
            await expect(
                page.locator(f"#search-results tr.result[data-ref='{story}'][data-matched=meaning]")
            ).to_have_count(1, timeout=SLOW)
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
            await in_project(page, f"{project.address}{item_address(story)}")
            await expect(page.locator("#item-title")).to_contain_text(
                f"{code_of(story)} ", timeout=SLOW
            )
            await open_editor(page)
            await page.locator("#edit-title").fill("Typed while the server was away")
            note, chip = page.locator("#connection-note"), page.locator("#connection")
            await expect(note).to_be_hidden()
            await expect(chip).to_be_hidden()

            # The server cannot be reached: the board says so on the page that is open, with a
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
            await expect(page.locator("#item-page")).to_be_visible()
            await themed(page, scheme)
            assert (
                await page.locator("#edit-title").input_value() == "Typed while the server was away"
            )
            # Saving says why it cannot be done, and the draft is still there.
            await page.get_by_role("button", name="Save changes").click()
            problem = page.locator("#item-details form.edit .problem")
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
            async with connect_as("agent-9", project) as by_agent:
                await ok(
                    by_agent,
                    "update_node",
                    node=story,
                    expected_version=1,
                    title=f"Renamed during the loss {word}",
                )

            # The server is back: the notice and the chip go, the board shows what was made
            # meanwhile, the draft stays and the agent's change to the item is offered.
            await page.unroute("**/board/api/**", lost)
            await expect(note).to_be_hidden(timeout=20_000)
            await expect(chip).to_be_hidden()
            await expect(page.locator(f"#epic-list a.tree-link[data-ref='{news}']")).to_be_visible(
                timeout=SLOW
            )
            assert (
                await page.locator("#edit-title").input_value() == "Typed while the server was away"
            )
            banner = page.locator("#item-details .notice.changed")
            await expect(banner).to_contain_text("This item changed (agent-9)", timeout=SLOW)
            await banner.get_by_role("button", name="Load the current version").click()
            await expect(page.locator("#edit-title")).to_have_value(
                f"Renamed during the loss {word}"
            )
            await expect(page.locator("#item-title")).to_contain_text(
                f"Renamed during the loss {word}"
            )
            await themed(page, scheme)

            # Lost again, the words are on every page the person goes to (each is drawn from what
            # the board already holds), and gone again when the server answers.
            await page.route("**/board/api/**", lost)
            await expect(note).to_be_visible(timeout=20_000)
            for view in ("graph", "roadmap", "board"):
                await w.go(f"#/{view}")
                await expect(page.locator("main#page h1").first).to_be_visible()
                await expect(note).to_be_visible()
                await expect(chip).to_be_visible()
                assert await page.locator("#panel, #drawer").count() == 0
                await themed(page, scheme)
            await page.unroute("**/board/api/**", lost)
            await expect(note).to_be_hidden(timeout=20_000)
            await expect(chip).to_be_hidden()


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
        note = await make_note(client, f"{hostile} {word}", hostile)
        decision = await make_note(
            client,
            f"Decision {hostile} {word}\n\n{body}",
            hostile,
            decided_by="Marie",
            nodes=[story],
        )
        await reindexed(client)
    async with themed_page(project.address, signed_in=True, scheme="light") as w:
        page = w.page
        await in_project(page, f"{project.address}#/board")
        for address in (
            "#/board",
            f"#/item?item={epic}&from=board",
            f"#/item?item={story}&from=graph",
            f"#/item?item=note:{note['id']}&from=search",
            f"#/item?item=note:{decision['id']}&from=activity",
            f"#/search?search={word}",
            "#/search?dups=1",
            "#/activity",
            "#/coverage",
            "#/graph",
            f"#/graph?tab=around&focus={story}",
            "#/lists?view=everything",
            "#/roadmap",
            "#/users",
            "#/tools",
            "#/status",
        ):
            await w.go(address)
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(300)
            for theme in THEMES:
                await switch(page, theme)
        # What was written is shown as the words it is, never as the markup it spells.
        await w.go(f"#/item?item={epic}&from=board")
        await expect(page.locator("#item-title")).to_contain_text("<img src=", timeout=SLOW)
        await expect(page.locator("#item-details .body-text")).to_contain_text(
            "<script>window.__pwned=3", timeout=SLOW
        )
        assert await page.locator("img, #item-page b, script:not([src]), style").count() == 0
        assert await page.evaluate("window.__pwned") is None
        # A link written with javascript: in it is not one that does anything.
        for link in await page.locator("#item-details .body-text a").all():
            assert not (await link.get_attribute("href") or "").lower().startswith("javascript")
        # The same on the page of a decision, whose text, why and first line are all written.
        await w.go(f"#/item?item=note:{decision['id']}&from=board")
        await expect(page.locator("#item-title")).to_contain_text("<img src=", timeout=SLOW)
        await expect(page.locator("#item-page")).to_contain_text("<script>window.__pwned=3")
        assert await page.locator("img, #item-page b, script:not([src]), style").count() == 0
        assert await page.evaluate("window.__pwned") is None
        for link in await page.locator("#item-page a").all():
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
async def test_late_answers_of_the_project_left_never_reach_the_one_entered_with_same_references(
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
        # Both projects number their items from 1: the same reference holds different things.
        epic_one = await made(one, "epic", f"Only in the first {word}")
        assert epic_one == await made(two, "epic", f"Only in the second {word}") == "epic:1"
        note_one = await make_note(one, f"A first note {word}", nodes=[epic_one])
        note_two = await make_note(two, f"A second note {word}", nodes=[epic_one])
        assert note_one["id"] == note_two["id"]
        async with themed_page(first.address, signed_in=True, scheme=scheme, keep_bodies=True) as w:
            page = w.page
            await in_project(page, f"{first.address}{item_address(epic_one)}")
            await expect(page.locator("#item-title")).to_contain_text(f"Only in the first {word}")
            # From now on the first project's answers are held back: the page's, the search's
            # and the poll's. An agent makes news in it, a search is on its way, and the person
            # leaves for the second.
            held = [
                await hold_answers(page, first, tool)
                for tool in ("get_node", "search", "changes_since", "recall", "list_nodes")
            ]
            await made(one, "epic", f"News for the first {word}")
            await held[2].arrived()
            await w.go(f"#/search?search=first+{word}")
            await held[1].arrived()
            before = len(w.requests)
            await page.locator("#project-switcher").select_option(label=f"Second {word}")
            await page.wait_for_url(re.compile(rf"/board/p/{second.id}(#.*)?$"), timeout=SLOW)
            await page.wait_for_selector("#app[data-shell=project]", timeout=SLOW)
            await w.go(f"{item_address(epic_one)}&from=search")
            await expect(page.locator("#item-title")).to_contain_text(
                f"Only in the second {word}", timeout=SLOW
            )
            for each in held:
                each.release.set()
            await asyncio.sleep(2)
            shown = await page.locator("body").inner_text()
            for gone in (
                f"Only in the first {word}",
                f"News for the first {word}",
                f"A first note {word}",
            ):
                assert gone not in shown, gone
            await expect(page.locator("#item-title")).to_contain_text(f"Only in the second {word}")
            # A search in the second project finds the second project's own, and nothing else.
            await w.go(f"#/search?search={word}")
            await expect(page.locator("#search-results tr.result")).to_have_count(2, timeout=SLOW)
            results = await page.locator("#search-results").inner_text()
            assert f"Only in the first {word}".lower() not in results.lower()
            assert f"A first note {word}".lower() not in results.lower()
            assert f"Only in the second {word}".lower() in results.lower()
            await switch(page, "dark" if scheme == "light" else "light")
            # The page asked only about the second project after it came to it, and no answer it
            # was given had a word of the first.
            assert not [url for url in w.requests[before:] if f"/projects/{first.id}/" in url]
            for url, text in w.bodies:
                if f"/projects/{second.id}/" in url:
                    assert f"Only in the first {word}" not in text
                    assert f"News for the first {word}" not in text
                    assert f"A first note {word}" not in text
            assert await policy_violations(page) == []

            # And when the session ends under an open project (it is ended from elsewhere), the
            # answers still on their way are dropped: the sign-in page stays as it is, with
            # nothing of the project on it and no error.
            late = [
                await hold_answers(page, second, tool) for tool in ("get_node", "recall", "search")
            ]
            epic_two = await made(two, "epic", f"Made as the session ended {word}")
            await expect(
                page.locator(f"#epic-list a.tree-link[data-ref='{epic_two}']")
            ).to_be_visible(timeout=SLOW)
            await w.go(f"#/search?search=second+{word}")
            await late[2].arrived()
            await w.go(f"{item_address(epic_two)}&from=search")
            await late[0].arrived()
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
            await expect(page.locator("#item-page")).to_have_count(0)
            await expect(page.locator("#search-results")).to_have_count(0)
            assert word not in await page.locator("body").inner_text()
            assert [problem for problem in w.problems if problem.startswith("page error")] == []
            await themed(page, "dark" if scheme == "light" else "light")
