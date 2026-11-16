"""M13's Markdown, references, backlinks, pick-list, quick switcher and graph layer against the
running containers, driven in a real headless browser and checked against a real MCP client (see
./check.sh). What the board shows as "Mentioned in" is compared with `find_mentions`, and what a
reference does to the graph (nothing) is read back through `list_links` and `get_node`. Titles carry
a unique word, and each test looks only at what it made, since all tests share one database."""

import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import PAGES, SLOW, board_url, browser_page, links_of, made
from tests.helpers import make_belief, make_decision, make_fact, ok, the_token, unique_word

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


def card(page: Page) -> Any:
    return page.locator("#panel .card-detail")


# -- 4: Markdown, and nothing that would run ----------------------------------------------------

BODY = """# A heading {word}

Some *emphasis*, some **strong** words and `inline code`.

- first point
- second point

1. one
2. two

> a quoted line

```
code {word}
```

[the docs](https://example.com/docs) and https://example.org/page

<script>window.hacked = 1</script>

<img src="http://other.invalid/p.png" onerror="window.hacked = 2">

![an image](http://other.invalid/picture.png)
"""


async def test_markdown_shows_structure_and_text_that_would_run_is_shown_as_text(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Markdown {word}")
    story = await made(client, "story", f"Written {word}", epic, body=BODY.format(word=word))
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/board?epic={epic}&card={story}")
        text = card(page).locator(".body-text .markdown")
        await expect(text).to_be_visible(timeout=SLOW)
        await expect(text.locator("h4")).to_have_text(f"A heading {word}")
        await expect(text.locator("ul > li")).to_have_text(["first point", "second point"])
        await expect(text.locator("ol > li")).to_have_text(["one", "two"])
        await expect(text.locator("em")).to_have_text("emphasis")
        await expect(text.locator("strong")).to_have_text("strong")
        await expect(text.locator("blockquote")).to_contain_text("a quoted line")
        await expect(text.locator("p code")).to_have_text("inline code")
        await expect(text.locator("pre code")).to_have_text(f"code {word}")

        # What would run or load is only text.
        shown = await text.inner_text()
        assert "<script>window.hacked = 1</script>" in shown
        assert '<img src="http://other.invalid/p.png"' in shown
        assert "![an image](http://other.invalid/picture.png)" in shown
        assert await text.locator("script, img, iframe, object, embed").count() == 0
        assert await page.evaluate("window.hacked === undefined")
        await page.wait_for_load_state("networkidle")
        foreign = [r for r in w.requests if not r.startswith(w.origin)]
        assert foreign == [], foreign
        assert w.dialogs == []

        # A web address is a link that opens in a new tab and that nothing follows by itself.
        docs = text.get_by_role("link", name="the docs")
        assert await docs.get_attribute("href") == "https://example.com/docs"
        assert await docs.get_attribute("target") == "_blank"
        assert "noopener" in (await docs.get_attribute("rel") or "")
        assert await text.get_by_role("link", name="https://example.org/page").count() == 1
        assert w.problems == []


async def test_rules_decisions_facts_and_beliefs_are_shown_as_markdown_too(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Everywhere {word}")
    story = await made(client, "story", f"Place {word}", epic)
    rule = await ok(
        client,
        "create_rule",
        actor="a",
        title=f"Rule {word}",
        story_id=story,
        body="A **bold** rule",
    )
    decision = await make_decision(
        client, f"Decision {word}", text="Use **PDF**", reason="Because *it is stable*"
    )
    fact = await make_fact(client, f"A **fact** about {word}", source="The `team` call")
    belief = await make_belief(client, [fact], f"A *belief* about {word}")
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/rules?rule={rule['id']}")
        await expect(
            page.locator(f".rule[data-rule='{rule['id']}'] .markdown strong")
        ).to_have_text("bold", timeout=SLOW)
        await w.go("#/memory")
        entry = page.locator(f"li.entry[data-entry='decision:{decision['id']}']")
        await expect(entry.locator(".markdown strong")).to_have_text("PDF", timeout=SLOW)
        await expect(entry.locator(".markdown em")).to_have_text("it is stable")
        fact_entry = page.locator(f"li.entry[data-entry='fact:{fact['id']}']")
        await expect(fact_entry.locator(".markdown strong")).to_have_text("fact")
        await expect(fact_entry.locator(".markdown code")).to_have_text("team")
        belief_entry = page.locator(f"li.entry[data-entry='belief:{belief['id']}']")
        await expect(belief_entry.locator(".markdown em")).to_have_text("belief")
        assert w.problems == []


# -- 5: references, titles, renames, missing ids, and backlinks -----------------------------------


async def test_a_reference_shows_the_current_title_follows_a_rename_and_creates_no_link(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"References {word}")
    target = await made(client, "story", f"Target {word}", epic)
    rule = await ok(client, "create_rule", actor="a", title=f"Ruled {word}", story_id=target)
    decision = await make_decision(client, f"Decided {word}")
    fact = await make_fact(client, f"Known {word}")
    belief = await make_belief(client, [fact], f"Believed {word}")
    missing = 987654321
    body = (
        f"See [[node:{target}]], [[rule:{rule['id']}]], [[decision:{decision['id']}]], "
        f"[[fact:{fact['id']}]], [[belief:{belief['id']}]] and [[node:{missing}]]."
    )
    mentioner = await made(client, "story", f"Mentioner {word}", epic, body=body)
    before_links = await links_of(client, target, mentioner)
    before_node = await ok(client, "get_node", node_id=mentioner)

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/board?epic={epic}&card={mentioner}")
        text = card(page).locator(".body-text .markdown")
        await expect(text.locator(".reference")).to_have_count(6, timeout=SLOW)

        def reference(kind: str, ident: int) -> Any:
            return text.locator(f".reference[data-kind={kind}][data-id='{ident}']")

        await expect(reference("node", target)).to_contain_text(f"#{target} Target {word}")
        await expect(reference("rule", rule["id"])).to_contain_text(f"Ruled {word}")
        await expect(reference("decision", decision["id"])).to_contain_text(f"Decided {word}")
        await expect(reference("fact", fact["id"])).to_contain_text(f"Known {word}")
        await expect(reference("belief", belief["id"])).to_contain_text(f"Believed {word}")
        for kind, ident in (("node", target), ("rule", rule["id"])):
            assert (await reference(kind, ident).get_attribute("href") or "").startswith("#/")
        # An id nothing has is shown plainly as missing, and is not a link.
        gone = reference("node", missing)
        await expect(gone).to_have_class(re.compile(r"\bmissing\b"))
        await expect(gone).to_have_text(f"[[node:{missing}]] (missing)")
        assert await gone.get_attribute("href") is None

        # A rename is followed by the reference without reloading the page.
        await ok(
            client,
            "update_node",
            actor="a",
            node_id=target,
            expected_version=1,
            title=f"Renamed {word}",
        )
        await expect(reference("node", target)).to_contain_text(f"Renamed {word}", timeout=SLOW)
        await expect(reference("node", target)).not_to_contain_text("Target")

        # Following the reference opens the item it names.
        await reference("node", target).click()
        await expect(card(page).locator("h2, h1").first).to_contain_text(
            f"Renamed {word}", timeout=SLOW
        )
        assert w.problems == []

    # Writing references made no link, and changed neither the status nor the version.
    assert await links_of(client, target, mentioner) == before_links
    after = await ok(client, "get_node", node_id=mentioner)
    assert (after["status"], after["version"]) == (before_node["status"], before_node["version"])
    assert after["status"] == "ready"


async def test_mentioned_in_equals_find_mentions_on_cards_rules_and_memory_and_follows_edits(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Backlinks {word}")
    target = await made(client, "story", f"Cited {word}", epic)
    rule = await ok(client, "create_rule", actor="a", title=f"Cited rule {word}", story_id=target)
    fact = await make_fact(client, f"Cited fact {word}")
    first = await made(client, "story", f"First {word}", epic, body=f"Refers to [[node:{target}]].")
    await ok(
        client,
        "update_rule",
        actor="a",
        rule_id=rule["id"],
        expected_version=1,
        body=f"Also [[node:{target}]] and [[fact:{fact['id']}]].",
    )
    decision = await make_decision(
        client,
        f"Deciding {word}",
        text=f"Because of [[fact:{fact['id']}]]",
        reason=f"Like [[rule:{rule['id']}]]",
    )

    def sources(page: dict[str, Any]) -> set[tuple[str, int]]:
        return {(m["source"]["item_type"], m["source"]["id"]) for m in page["items"]}

    async with browser_page(board, token=the_token()) as w:
        page = w.page
        # On the card of a story.
        await w.open(f"#/board?epic={epic}&card={target}")
        section = card(page).locator("section[data-part=mentions]")
        listed = await ok(client, "find_mentions", node_id=target)
        await expect(section.locator("li.mention")).to_have_count(
            len(listed["items"]), timeout=SLOW
        )
        shown = await section.locator("li.mention").evaluate_all(
            "els => els.map(e => e.dataset.source)"
        )
        assert sorted(shown) == sorted(f"{kind}:{ident}" for kind, ident in sources(listed))
        assert ("story", first) in sources(listed) and ("rule", rule["id"]) in sources(listed)
        await expect(section.locator("h3")).to_have_text(f"Mentioned in ({len(listed['items'])})")

        # A new mention written by an agent shows up without reloading.
        second = await made(client, "task", f"Second {word}", first, body=f"[[node:{target}]]")
        await expect(section.locator("li.mention")).to_have_count(
            len(listed["items"]) + 1, timeout=SLOW
        )
        await expect(section.locator(f"li.mention[data-source='task:{second}']")).to_be_visible()
        again = await ok(client, "find_mentions", node_id=target)
        assert ("task", second) in sources(again)

        # On the Rules page, closed until it is opened, then the same as find_mentions.
        await w.go(f"#/rules?rule={rule['id']}")
        details = page.locator(f".rule[data-rule='{rule['id']}'] details.mentioned-in")
        await expect(details).to_be_visible(timeout=SLOW)
        await details.locator("summary").click()
        by_rule = await ok(client, "find_mentions", rule_id=rule["id"])
        await expect(details.locator("li.mention")).to_have_count(
            len(by_rule["items"]), timeout=SLOW
        )
        assert ("decision", decision["id"]) in sources(by_rule)
        assert sorted(
            await details.locator("li.mention").evaluate_all(
                "els => els.map(e => e.dataset.source)"
            )
        ) == sorted(f"{kind}:{ident}" for kind, ident in sources(by_rule))

        # On the Memory page, for a fact.
        await w.go(f"#/memory?entry=fact:{fact['id']}")
        entry = page.locator(f"li.entry[data-entry='fact:{fact['id']}'] details.mentioned-in")
        await expect(entry).to_be_visible(timeout=SLOW)
        await entry.locator("summary").click()
        by_fact = await ok(client, "find_mentions", memory_id=fact["id"])
        await expect(entry.locator("li.mention")).to_have_count(len(by_fact["items"]), timeout=SLOW)
        assert {("rule", rule["id"]), ("decision", decision["id"])} <= sources(by_fact)
        assert w.problems == []


# -- the pick-list in the editor -----------------------------------------------------------------


async def test_the_editor_offers_a_pick_list_after_two_brackets_and_writes_only_text(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Picking {word}")
    target = await made(client, "story", f"Pickable {word}", epic)
    writer = await made(client, "story", f"Writer {word}", epic)
    before = await links_of(client, target, writer)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/board?epic={epic}&card={writer}")
        box = page.locator("#edit-body")
        await box.click()
        await page.keyboard.type(f"Depends on [[pickable {word}")
        picker = page.locator(".ref-picker")
        await expect(picker).to_be_visible(timeout=SLOW)
        option = picker.locator(f"[data-ref='[[node:{target}]]']")
        await expect(option).to_contain_text(f"Pickable {word}")
        # Nothing is written to the server by choosing.
        await page.keyboard.press("Enter")
        await expect(picker).to_be_hidden()
        assert await box.input_value() == f"Depends on [[node:{target}]]"
        assert (await ok(client, "get_node", node_id=writer))["body"] == ""
        await page.get_by_role("button", name="Save changes").click()
        await expect(card(page).locator(".body-text .reference")).to_contain_text(
            f"Pickable {word}", timeout=SLOW
        )
        assert (await ok(client, "get_node", node_id=writer))[
            "body"
        ] == f"Depends on [[node:{target}]]"
        mentions = await ok(client, "find_mentions", node_id=target)
        assert [(m["source"]["item_type"], m["source"]["id"]) for m in mentions["items"]] == [
            ("story", writer)
        ]
        # Typing an id narrows the list to it, and Escape closes the list and keeps the text.
        await box.click()
        await page.keyboard.press("Control+End")
        await page.keyboard.type(f" and [[node:{target}")
        await expect(picker.locator("[role=option]")).to_have_count(1, timeout=SLOW)
        await page.keyboard.press("Escape")
        await expect(picker).to_be_hidden()
        assert (await box.input_value()).endswith(f"[[node:{target}")
        assert w.problems == []
    assert await links_of(client, target, writer) == before


# -- 6: the quick switcher ---------------------------------------------------------------------


async def test_the_quick_switcher_opens_from_the_keyboard_on_every_page(
    client: Client, board: str
) -> None:
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        for name in PAGES:
            await w.open(f"#/{name}")
            await page.wait_for_selector("#page", timeout=SLOW)
            await page.keyboard.press("Control+k")
            await expect(page.locator("#switcher")).to_be_visible(timeout=SLOW)
            await expect(page.locator("#switcher-input")).to_be_focused()
            await page.keyboard.press("Escape")
            await expect(page.locator("#switcher")).to_have_count(0)
        # The other way of writing the shortcut, and the visible button.
        await page.keyboard.press("Meta+k")
        await expect(page.locator("#switcher")).to_be_visible()
        await page.keyboard.press("Escape")
        await page.locator("#switcher-open").click()
        await expect(page.locator("#switcher")).to_be_visible()
        assert w.problems == []


async def test_the_quick_switcher_jumps_by_a_title_word_by_id_and_to_a_page(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Switching {word}")
    story = await made(client, "story", f"Findable {word}", epic)
    rule = await ok(
        client, "create_rule", actor="a", title=f"Switchable rule {word}", story_id=story
    )
    decision = await make_decision(client, f"Switchable decision {word}")
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/table")
        # By a word of the title.
        await page.keyboard.press("Control+k")
        await page.keyboard.type(f"findable {word}")
        first = page.locator("#switcher [role=option]").first
        await expect(first).to_have_attribute("data-ref", f"node:{story}", timeout=SLOW)
        await page.keyboard.press("Enter")
        await expect(page.locator("#switcher")).to_have_count(0)
        await expect(card(page)).to_contain_text(f"Findable {word}", timeout=SLOW)
        assert f"card={story}" in await page.evaluate("location.hash")

        # By id, from another page, in the plain and the typed forms.
        await w.go("#/overlaps")
        await page.keyboard.press("Control+k")
        await page.keyboard.type(f"#{epic}")
        await expect(page.locator("#switcher [role=option]").first).to_have_attribute(
            "data-ref", f"node:{epic}", timeout=SLOW
        )
        await page.keyboard.press("Enter")
        await expect(card(page)).to_contain_text(f"Switching {word}", timeout=SLOW)

        await w.go("#/search")
        await page.keyboard.press("Control+k")
        await page.keyboard.type(f"rule {rule['id']}")
        await expect(page.locator("#switcher [role=option]")).to_have_count(1, timeout=SLOW)
        await page.keyboard.press("Enter")
        await expect(page.locator(f".rule[data-rule='{rule['id']}']")).to_be_visible(timeout=SLOW)
        assert "#/rules" in await page.evaluate("location.hash")

        await w.go("#/board")
        await page.keyboard.press("Control+k")
        await page.keyboard.type(f"decision {decision['id']}")
        await expect(page.locator("#switcher [role=option]")).to_have_count(1, timeout=SLOW)
        await page.keyboard.press("Enter")
        await expect(
            page.locator(f"li.entry[data-entry='decision:{decision['id']}']")
        ).to_be_visible(timeout=SLOW)

        # To a page, with the arrow keys and Enter.
        await page.keyboard.press("Control+k")
        await page.keyboard.type("overl")
        await expect(page.locator("#switcher [data-page=overlaps]")).to_be_visible()
        await page.keyboard.press("Enter")
        await expect(page.locator("h1")).to_have_text("Overlaps", timeout=SLOW)
        assert "#/overlaps" in await page.evaluate("location.hash")

        # Nothing matching says so, and Enter then goes nowhere.
        await page.keyboard.press("Control+k")
        await page.keyboard.type(f"zz{word}")
        await expect(page.locator("#switcher")).to_contain_text("Nothing matches")
        await page.keyboard.press("Enter")
        await expect(page.locator("#switcher")).to_be_visible()
        assert w.problems == []


# -- the graph's mentions layer ------------------------------------------------------------------


async def test_the_graph_draws_a_mentions_layer_that_is_off_until_chosen_and_equals_find_mentions(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Layered {word}")
    cited = await made(client, "story", f"Cited {word}", epic)
    citing = await made(
        client,
        "story",
        f"Citing {word}",
        epic,
        body=f"Needs [[node:{cited}]] and [[node:987654321]]",
    )
    rule = await ok(
        client,
        "create_rule",
        actor="a",
        title=f"Rule {word}",
        story_id=cited,
        body=f"Mentions [[node:{citing}]]",
    )
    before = await links_of(client, cited, citing)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        svg = page.locator("svg#graph-svg")
        await w.open(f"#/graph?epic={epic}&show=rules")
        await expect(svg.locator(f".dot[data-key='n:{citing}']")).to_be_visible(timeout=SLOW)
        assert await svg.locator(".edge[data-type='mention']").count() == 0
        await page.locator("[name=mentions]").check()
        mention = svg.locator(
            f".edge[data-type='mention'][data-source='n:{citing}'][data-target='n:{cited}']"
        )
        await expect(mention).to_have_count(1, timeout=SLOW)
        await expect(
            svg.locator(
                f".edge[data-type='mention'][data-source='r:{rule['id']}'][data-target='n:{citing}']"
            )
        ).to_have_count(1)
        # A reference to something that does not exist draws nothing.
        await expect(svg.locator(".edge[data-type='mention']")).to_have_count(2)
        assert "mentions=1" in await page.evaluate("location.hash")
        assert w.problems == []
    assert await links_of(client, cited, citing) == before
