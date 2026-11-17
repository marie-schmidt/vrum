"""M16's Markdown, references, backlinks, pick-list, quick switcher and the Graph's mentions layer
against the running containers, driven in a real headless browser and checked against a real MCP
client (see ./check.sh). A reference is `[[epic:3]]`, `[[story:12]]`, `[[task:40]]`, `[[rule:3]]`,
`[[decision:7]]` or `[[fact:5]]` and is only text (`[[node:12]]` is not a reference any more): what
the board shows as "Mentioned in" is compared with `find_mentions`, and what a reference does to
the graph (nothing) is read back through `list_links` and `get_node`. Each test works in a project
of its own. Work is named by its reference in every call and in the hooks of the page
(`data-ref`); the address names an epic by its number (`epic=3`)."""

import re
from typing import Any

import pytest
from playwright.async_api import Page, expect

from tests.board_context import (
    ADA,
    dialog,
    edge,
    fresh_project,
    graph_open,
    hash_params,
    listed,
    listed_connections,
    map_edges,
    panel,
    show,
    svg,
    tally,
)
from tests.board_helpers import PAGES, SLOW, browser_page, links_of, made, open_editor
from tests.helpers import code_of, make_decision, make_fact, ok, unique_word
from tests.helpers import number_of as number
from tests.world import Project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _browser(board: str, project: Project) -> Any:
    return browser_page(board, token=project.key, name=ADA)


def card(page: Page) -> Any:
    return panel(page).locator(".card-detail")


def sources(page_of_mentions: dict[str, Any]) -> set[str]:
    """The references of what the page of mentions says mentions the item."""
    return {m["source"]["ref"] for m in page_of_mentions["items"]}


# -- Markdown, and nothing that would run -------------------------------------------------------

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
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Markdown {word}")
        story = await made(client, "story", f"Written {word}", epic, body=BODY.format(word=word))
        async with _browser(board, project) as w:
            page = w.page
            await w.open(f"#/board?epic={number(epic)}&sel={story}")
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


async def test_requirements_decisions_and_facts_are_shown_as_markdown_too(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Everywhere {word}")
        story = await made(client, "story", f"Place {word}", epic)
        rule = await ok(
            client,
            "create_rule",
            actor="a",
            title=f"Rule {word}",
            story=story,
            body="A **bold** requirement",
        )
        decision = await make_decision(
            client,
            f"Decision {word}",
            text="Use **PDF**",
            reason="Because *it is stable*",
            nodes=[story],
        )
        fact = await make_fact(
            client, f"A **fact** about {word}", source="The `team` call", nodes=[story]
        )
        async with _browser(board, project) as w:
            page = w.page
            here = f"#/graph?view=context&as=list&epic={number(epic)}"
            await w.open(f"{here}&sel=rule:{rule['id']}")
            await expect(card(page)).to_have_count(0)
            await expect(
                panel(page).locator(".rule-detail .body-text .markdown strong")
            ).to_have_text("bold", timeout=SLOW)

            await w.go(f"{here}&sel=decision:{decision['id']}")
            record = panel(page).locator(f".record-detail[data-record='decision:{decision['id']}']")
            await expect(record.locator(".record-text strong")).to_have_text("PDF", timeout=SLOW)
            await expect(record.locator(".record-reason em")).to_have_text("it is stable")

            await w.go(f"{here}&sel=fact:{fact['id']}")
            record = panel(page).locator(f".record-detail[data-record='fact:{fact['id']}']")
            await expect(record.locator(".record-text strong")).to_have_text("fact", timeout=SLOW)
            await expect(record.locator(".record-source code")).to_have_text("team")

            # The same entries, as recall gives them on the story's panel.
            await w.go(f"{here}&sel={story}")
            entry = card(page).locator(f"li.record[data-record='fact:{fact['id']}']")
            await expect(entry.locator(".record-text strong")).to_have_text("fact", timeout=SLOW)
            entry = card(page).locator(f"li.record[data-record='decision:{decision['id']}']")
            await expect(entry.locator(".record-text strong")).to_have_text("PDF")
            assert w.problems == []


# -- references, titles, renames, missing numbers ------------------------------------------------


async def test_a_reference_shows_the_current_title_follows_a_rename_and_creates_no_link(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"References {word}")
        target = await made(client, "story", f"Target {word}", epic)
        rule = await ok(client, "create_rule", actor="a", title=f"Ruled {word}", story=target)
        decision = await make_decision(client, f"Decided {word}")
        fact = await make_fact(client, f"Known {word}")
        missing = f"story:{987654321}"
        # A belief is not a thing any more, so its reference is only words; neither is a node
        # (`[[node:12]]` named any epic, story or task once, and now is only text).
        body = (
            f"See [[{target}]], [[rule:{rule['id']}]], [[decision:{decision['id']}]], "
            f"[[fact:{fact['id']}]], [[belief:3]], [[node:{number(target)}]] and [[{missing}]]."
        )
        mentioner = await made(client, "story", f"Mentioner {word}", epic, body=body)
        before_links = await links_of(client, target, mentioner)
        before_node = await ok(client, "get_node", node=mentioner)

        async with _browser(board, project) as w:
            page = w.page
            await w.open(f"#/board?epic={number(epic)}&sel={mentioner}")
            text = card(page).locator(".body-text .markdown")
            await expect(text.locator(".reference")).to_have_count(5, timeout=SLOW)

            def reference(ref: str) -> Any:
                return text.locator(f".reference[data-ref='{ref}']")

            await expect(reference(target)).to_contain_text(f"{code_of(target)} Target {word}")
            await expect(reference(f"rule:{rule['id']}")).to_contain_text(f"Ruled {word}")
            await expect(reference(f"decision:{decision['id']}")).to_contain_text(f"Decided {word}")
            await expect(reference(f"fact:{fact['id']}")).to_contain_text(f"Known {word}")
            for ref in (target, f"rule:{rule['id']}"):
                assert (await reference(ref).get_attribute("href") or "").startswith("#/")
            # A number nothing has is shown plainly as missing, and is not a link.
            gone = reference(missing)
            await expect(gone).to_have_class(re.compile(r"\bmissing\b"))
            await expect(gone).to_have_text(f"[[{missing}]] (missing)")
            assert await gone.get_attribute("href") is None
            # [[belief:3]] and [[node:N]] are not references: each stays the text it is.
            shown = await text.inner_text()
            assert "[[belief:3]]" in shown and f"[[node:{number(target)}]]" in shown
            assert await text.locator(".reference[data-kind=belief]").count() == 0
            assert await text.locator(".reference[data-kind=node]").count() == 0

            # A rename is followed by the reference without reloading the page.
            await ok(
                client,
                "update_node",
                actor="a",
                node=target,
                expected_version=1,
                title=f"Renamed {word}",
            )
            await expect(reference(target)).to_contain_text(f"Renamed {word}", timeout=SLOW)
            await expect(reference(target)).not_to_contain_text("Target")
            await ok(
                client,
                "update_rule",
                actor="a",
                rule_id=rule["id"],
                expected_version=1,
                title=f"Reworded {word}",
            )
            await expect(reference(f"rule:{rule['id']}")).to_contain_text(
                f"Reworded {word}", timeout=SLOW
            )

            # Following a reference opens the item it names, in the same panel, with the page
            # left as it was.
            here = f"#/board?epic={number(epic)}&sel={mentioner}"
            await reference(target).click()
            await expect(card(page).locator("h2").first).to_contain_text(
                f"Renamed {word}", timeout=SLOW
            )
            assert (await hash_params(page))["sel"] == target
            await w.go(here)
            await reference(f"rule:{rule['id']}").click()
            await expect(panel(page).locator(".rule-detail h2")).to_contain_text(
                f"Reworded {word}", timeout=SLOW
            )
            await w.go(here)
            await reference(f"decision:{decision['id']}").click()
            await expect(
                panel(page).locator(f".record-detail[data-record='decision:{decision['id']}']")
            ).to_be_visible(timeout=SLOW)
            await w.go(here)
            await reference(f"fact:{fact['id']}").click()
            await expect(
                panel(page).locator(f".record-detail[data-record='fact:{fact['id']}']")
            ).to_be_visible(timeout=SLOW)
            assert w.problems == []

        # Writing references made no link, and changed neither the status nor the version.
        assert await links_of(client, target, mentioner) == before_links
        after = await ok(client, "get_node", node=mentioner)
        assert (after["status"], after["version"]) == (
            before_node["status"],
            before_node["version"],
        )
        assert after["status"] == "ready"


# -- "Mentioned in" is find_mentions ------------------------------------------------------------


async def test_mentioned_in_equals_find_mentions_in_the_panel_and_the_list_and_follows_edits(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Backlinks {word}")
        target = await made(client, "story", f"Cited {word}", epic)
        rule = await ok(client, "create_rule", actor="a", title=f"Cited rule {word}", story=target)
        fact = await make_fact(client, f"Cited fact {word}", nodes=[target])
        first = await made(client, "story", f"First {word}", epic, body=f"Refers to [[{target}]].")
        await ok(
            client,
            "update_rule",
            actor="a",
            rule_id=rule["id"],
            expected_version=1,
            body=f"Also [[{target}]] and [[fact:{fact['id']}]].",
        )
        decision = await make_decision(
            client,
            f"Deciding {word}",
            text=f"Because of [[fact:{fact['id']}]]",
            reason=f"Like [[rule:{rule['id']}]]",
            nodes=[target],
        )

        async def panel_lists(page: Page, key: str, want: dict[str, Any]) -> None:
            """The panel's "Mentioned in" for this record says what find_mentions says."""
            section = panel(page).locator("section[data-part=mentions]")
            await expect(section.locator("h3")).to_have_text(
                f"Mentioned in ({len(want['items'])})", timeout=SLOW
            )
            await expect(section.locator("li.mention")).to_have_count(len(want["items"]))
            shown = await section.locator("li.mention").evaluate_all(
                "els => els.map(e => e.dataset.source)"
            )
            assert sorted(shown) == sorted(sources(want)), key

        async with _browser(board, project) as w:
            page = w.page
            # On the panel of a story.
            await w.open(f"#/board?epic={number(epic)}&sel={target}")
            listed_now = await ok(client, "find_mentions", node=target)
            await panel_lists(page, "story", listed_now)
            assert first in sources(listed_now)
            assert f"rule:{rule['id']}" in sources(listed_now)

            # A new mention written by an agent shows up without reloading.
            second = await made(client, "task", f"Second {word}", first, body=f"[[{target}]]")
            section = panel(page).locator("section[data-part=mentions]")
            await expect(section.locator("li.mention")).to_have_count(
                len(listed_now["items"]) + 1, timeout=SLOW
            )
            await expect(section.locator(f"li.mention[data-source='{second}']")).to_be_visible()
            assert second in sources(await ok(client, "find_mentions", node=target))

            # On the panel of a requirement, of a decision and of a fact, from the Graph.
            for key, argument, ident in (
                (f"rule:{rule['id']}", "rule_id", rule["id"]),
                (f"fact:{fact['id']}", "fact_id", fact["id"]),
                (f"decision:{decision['id']}", "decision_id", decision["id"]),
            ):
                await w.go(f"#/graph?view=context&as=list&epic={number(epic)}&sel={key}")
                want = await ok(client, "find_mentions", **{argument: ident})
                await panel_lists(page, key, want)
            by_rule = await ok(client, "find_mentions", rule_id=rule["id"])
            assert f"decision:{decision['id']}" in sources(by_rule)
            by_fact = await ok(client, "find_mentions", fact_id=fact["id"])
            assert {f"rule:{rule['id']}", f"decision:{decision['id']}"} <= sources(by_fact)
            await w.go(
                f"#/graph?view=context&as=list&epic={number(epic)}&sel=decision:{decision['id']}"
            )
            await expect(panel(page).locator("section[data-part=mentions] .none")).to_have_text(
                "Nothing refers to it.", timeout=SLOW
            )

            # In the Graph's list, a row's "Mentioned in" is closed until it is opened, and then
            # the same.
            await w.go(f"#/graph?view=context&as=list&epic={number(epic)}")
            for kind, ident, argument in (
                ("rule", rule["id"], "rule_id"),
                ("fact", fact["id"], "fact_id"),
            ):
                details = listed(page, f"{kind}:{ident}").locator("details.mentioned-in")
                await expect(details).to_be_visible(timeout=SLOW)
                await expect(details).not_to_have_attribute("open", "")
                await details.locator("summary").click()
                want = await ok(client, "find_mentions", **{argument: ident})
                await expect(details.locator("li.mention")).to_have_count(
                    len(want["items"]), timeout=SLOW
                )
                shown = await details.locator("li.mention").evaluate_all(
                    "els => els.map(e => e.dataset.source)"
                )
                assert sorted(shown) == sorted(sources(want))
            assert w.problems == []


# -- the pick-list in the editor -----------------------------------------------------------------


async def test_the_editor_offers_a_pick_list_after_two_brackets_and_writes_only_text(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Picking {word}")
        target = await made(client, "story", f"Pickable {word}", epic)
        writer = await made(client, "story", f"Writer {word}", epic)
        rule = await ok(client, "create_rule", actor="a", title=f"Pickable rule {word}")
        fact = await make_fact(client, f"Pickable fact {word}")
        decision = await make_decision(client, f"Pickable decision {word}")
        before = await links_of(client, target, writer)
        async with _browser(board, project) as w:
            page = w.page
            await w.open(f"#/board?epic={number(epic)}&sel={writer}")
            await open_editor(page)
            box = page.locator("#edit-body")
            await box.click()
            await page.keyboard.type(f"Depends on [[pickable {word}")
            picker = page.locator(".ref-picker")
            await expect(picker).to_be_visible(timeout=SLOW)
            # Every kind of thing a reference can name is offered, by a word of its title.
            for ref in (
                f"[[{target}]]",
                f"[[rule:{rule['id']}]]",
                f"[[decision:{decision['id']}]]",
                f"[[fact:{fact['id']}]]",
            ):
                await expect(picker.locator(f"[data-ref='{ref}']")).to_have_count(1, timeout=SLOW)
            assert await picker.locator("[data-ref^='[[belief']").count() == 0
            assert await picker.locator("[data-ref^='[[node:']").count() == 0
            # Nothing is written to the server by choosing.
            await picker.locator(f"[data-ref='[[{target}]]']").hover()
            for _ in range(4):
                if (
                    await picker.locator("[aria-selected=true]").get_attribute("data-ref")
                    == f"[[{target}]]"
                ):
                    break
                await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await expect(picker).to_be_hidden()
            assert await box.input_value() == f"Depends on [[{target}]]"
            assert (await ok(client, "get_node", node=writer))["body"] == ""
            await page.get_by_role("button", name="Save changes").click()
            await expect(card(page).locator(".body-text .reference")).to_contain_text(
                f"Pickable {word}", timeout=SLOW
            )
            assert (await ok(client, "get_node", node=writer))["body"] == (
                f"Depends on [[{target}]]"
            )
            mentions = await ok(client, "find_mentions", node=target)
            assert sources(mentions) == {writer}
            # Typing the kind and an id narrows the list to it, and Escape closes the list and
            # keeps the text.
            await open_editor(page)
            await box.click()
            await page.keyboard.press("Control+End")
            await page.keyboard.type(f" and [[fact:{fact['id']}")
            await expect(picker.locator("[role=option]")).to_have_count(1, timeout=SLOW)
            await expect(picker.locator("[role=option]")).to_have_attribute(
                "data-ref", f"[[fact:{fact['id']}]]"
            )
            await page.keyboard.press("Escape")
            await expect(picker).to_be_hidden()
            assert (await box.input_value()).endswith(f"[[fact:{fact['id']}")

            # The same pick-list in the dialog that records a fact: it writes text, and nothing
            # is recorded until the dialog is sent.
            counts = await tally(client)
            await w.go(f"#/graph?view=context&as=list&epic={number(epic)}")
            await page.locator("#record-fact").click()
            text = dialog(page).locator("#record-fact-text")
            await text.click()
            await page.keyboard.type(f"Follows [[rule:{rule['id']}")
            await expect(page.locator(".ref-picker [role=option]")).to_have_count(1, timeout=SLOW)
            await page.keyboard.press("Enter")
            assert await text.input_value() == f"Follows [[rule:{rule['id']}]]"
            await dialog(page).get_by_role("button", name="Cancel").click()
            assert await tally(client) == counts
            assert w.problems == []
        assert await links_of(client, target, writer) == before


# -- the quick switcher --------------------------------------------------------------------------


async def test_the_quick_switcher_opens_from_the_keyboard_on_every_view_and_drawer(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        await made(client, "epic", "Switch")
        async with _browser(board, project) as w:
            page = w.page
            addresses = [
                *(f"#/{name}" for name in PAGES),
                "#/board?drawer=search",
                "#/graph?drawer=activity",
                "#/graph?view=context&as=list",
                "#/roadmap?drawer=search",
            ]
            for address in addresses:
                await w.open(address)
                await page.wait_for_selector("#page", timeout=SLOW)
                await page.keyboard.press("Control+k")
                await expect(page.locator("#switcher")).to_be_visible(timeout=SLOW)
                await expect(page.locator("#switcher-input")).to_be_focused()
                await page.keyboard.press("Escape")
                await expect(page.locator("#switcher")).to_have_count(0)
                if "drawer=" in address:
                    await expect(page.locator("#drawer")).to_be_visible()
            # The other way of writing the shortcut, and the visible button.
            await page.keyboard.press("Meta+k")
            await expect(page.locator("#switcher")).to_be_visible()
            await page.keyboard.press("Escape")
            await page.locator("#switcher-open").click()
            await expect(page.locator("#switcher")).to_be_visible()
            assert w.problems == []


async def test_the_quick_switcher_jumps_by_a_title_word_by_code_and_to_a_view_or_drawer(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Switching {word}")
        story = await made(client, "story", f"Findable {word}", epic)
        task = await made(client, "task", f"Doable {word}", story)
        rule = await ok(
            client, "create_rule", actor="a", title=f"Switchable rule {word}", story=story
        )
        decision = await make_decision(client, f"Switchable decision {word}")
        fact = await make_fact(client, f"Switchable fact {word}")
        async with _browser(board, project) as w:
            page = w.page
            await w.open("#/roadmap")
            # By a word of the title: the item opens in the panel of the view the person is on.
            await page.keyboard.press("Control+k")
            await page.keyboard.type(f"findable {word}")
            first = page.locator("#switcher [role=option]").first
            await expect(first).to_have_attribute("data-ref", story, timeout=SLOW)
            await page.keyboard.press("Enter")
            await expect(page.locator("#switcher")).to_have_count(0)
            await expect(card(page)).to_contain_text(f"Findable {word}", timeout=SLOW)
            assert (await hash_params(page))["sel"] == story
            assert "#/roadmap" in await page.evaluate("location.hash")

            # By code, as the page writes it.
            await w.go("#/board")
            await page.keyboard.press("Control+k")
            await page.keyboard.type(code_of(epic))
            await expect(page.locator("#switcher [role=option]").first).to_have_attribute(
                "data-ref", epic, timeout=SLOW
            )
            await page.keyboard.press("Enter")
            await expect(card(page)).to_contain_text(f"Switching {word}", timeout=SLOW)

            # Each kind is numbered on its own, so a bare number finds every kind that has it: in
            # a project of its own the first epic, story, task, requirement, decision and fact
            # are all number 1, and nothing else is.
            assert [number(ref) for ref in (epic, story, task)] == [1, 1, 1]
            assert [rule["id"], decision["id"], fact["id"]] == [1, 1, 1]
            await w.go("#/board")
            await page.keyboard.press("Control+k")
            await page.keyboard.type("1")
            options = page.locator("#switcher [role=option]")
            await expect(options).to_have_count(6, timeout=SLOW)
            assert await options.evaluate_all("els => els.map(e => e.dataset.ref)") == [
                epic,
                story,
                task,
                f"rule:{rule['id']}",
                f"decision:{decision['id']}",
                f"fact:{fact['id']}",
            ]
            await page.keyboard.press("Escape")
            await expect(page.locator("#switcher")).to_have_count(0)

            # By kind and number: a requirement, a decision, a fact.
            for typed, key in (
                (f"requirement {rule['id']}", f"rule:{rule['id']}"),
                (f"decision {decision['id']}", f"decision:{decision['id']}"),
                (f"fact {fact['id']}", f"fact:{fact['id']}"),
            ):
                await w.go("#/graph?view=context&as=list")
                await page.keyboard.press("Control+k")
                await page.keyboard.type(typed)
                await expect(page.locator("#switcher [role=option]")).to_have_count(1, timeout=SLOW)
                await page.keyboard.press("Enter")
                await expect(panel(page).locator(f"[data-record='{key}']").first).to_be_visible(
                    timeout=SLOW
                )
                assert (await hash_params(page))["sel"] == key
                assert "#/graph" in await page.evaluate("location.hash")

            # To a view, and to the drawers, with the keyboard.
            await w.go("#/board")
            await page.keyboard.press("Control+k")
            await page.keyboard.type("graph")
            await expect(page.locator("#switcher [data-page=graph]")).to_be_visible()
            await page.keyboard.press("Enter")
            await expect(page.locator("#graph-page")).to_be_visible(timeout=SLOW)
            assert "#/graph" in await page.evaluate("location.hash")
            await page.keyboard.press("Control+k")
            await page.keyboard.type("activity")
            await expect(page.locator("#switcher [data-page=activity]")).to_be_visible()
            await page.keyboard.press("Enter")
            await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible(timeout=SLOW)
            await page.keyboard.press("Control+k")
            await page.keyboard.type("search")
            await expect(page.locator("#switcher [data-page=search]")).to_be_visible()
            await page.keyboard.press("Enter")
            await expect(page.locator("#drawer[data-drawer=search]")).to_be_visible(timeout=SLOW)

            # Nothing matching says so, and Enter then goes nowhere.
            await page.keyboard.press("Control+k")
            await page.keyboard.type(f"zz{word}")
            await expect(page.locator("#switcher")).to_contain_text("Nothing matches")
            await page.keyboard.press("Enter")
            await expect(page.locator("#switcher")).to_be_visible()
            assert w.problems == []


# -- the graph's mentions layer ------------------------------------------------------------------


async def test_the_graph_draws_a_mentions_layer_that_is_off_until_chosen_and_equals_find_mentions(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Layered {word}")
        cited = await made(client, "story", f"Cited {word}", epic)
        citing = await made(
            client,
            "story",
            f"Citing {word}",
            epic,
            body=f"Needs [[{cited}]] and [[story:987654321]]",
        )
        rule = await ok(
            client,
            "create_rule",
            actor="a",
            title=f"Rule {word}",
            story=cited,
            body=f"Mentions [[{citing}]]",
        )
        decision = await make_decision(
            client, f"Decision {word}", text=f"After [[rule:{rule['id']}]]", nodes=[cited]
        )
        fact = await make_fact(
            client, f"Fact {word}", source=f"See [[decision:{decision['id']}]]", nodes=[cited]
        )
        before = await links_of(client, cited, citing)
        everything = await ok(client, "find_mentions", limit=200)
        # Every reference to something that exists, as a line from the record that holds it: the
        # references name every kind the same way, so they are the keys the Graph uses.
        wanted = {
            ("mention", m["source"]["ref"], m["target"]["ref"])
            for m in everything["items"]
            if m["target"]["exists"]
        }
        assert len(wanted) == 4 and len(everything["items"]) == 5  # one points at nothing
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=everything&epic={number(epic)}")
            await expect(svg(page).locator(f".dot[data-key='{citing}']")).to_be_visible(
                timeout=SLOW
            )
            assert await svg(page).locator(".edge[data-type='mention']").count() == 0
            await page.locator("[name=mentions]").check()
            await expect(svg(page).locator(".edge[data-type='mention']")).to_have_count(
                len(wanted), timeout=SLOW
            )
            drawn = {line for line in await map_edges(page) if line[0] == "mention"}
            assert drawn == wanted
            assert "mentions=1" in await page.evaluate("location.hash")
            await expect(edge(page, "mention", citing, cited)).to_have_count(1)
            await expect(
                edge(page, "mention", f"fact:{fact['id']}", f"decision:{decision['id']}")
            ).to_have_count(1)
            # The list says the same lines, in the connections of its rows.
            await show(page, presentation="list")
            await expect(listed(page, f"fact:{fact['id']}")).to_be_visible(timeout=SLOW)
            said = await listed_connections(page)
            for line in wanted:
                assert line in said or (line[0], line[2], line[1]) in said, line
            await show(page, presentation="map")
            await page.locator("[name=mentions]").uncheck()
            await expect(svg(page).locator(".edge[data-type='mention']")).to_have_count(0)
            assert w.problems == []
        assert await links_of(client, cited, citing) == before
