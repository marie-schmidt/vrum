"""Markdown, references, backlinks, the pick-list, the quick switcher and the mentions of the Graph
against the running containers, driven in a real headless browser and checked against a real MCP
client (see ./check.sh). A reference is `[[epic:3]]`, `[[story:12]]`, `[[task:40]]`, `[[rule:3]]`
or `[[note:7]]` and is only text (`[[decision:7]]`, `[[fact:5]]` and `[[node:12]]` are not
references any more): what the board shows as "Mentioned in" is compared with `find_mentions`, and
what a reference does to the graph (nothing) is read back through `list_links` and `get_node`. A
reference, a mention and a quick-switcher result are links to the page of the record they name
(nothing opens beside a page), and the page of each kind of record shows its Markdown and its
mentions. The Graph draws the mentions between work as lines of its Structure map when asked, and
names them in Around. Each test works in a project of its own. Work is named by its reference in
every call and in the hooks of the page (`data-ref`); the address names an epic by its number
(`epic=3`)."""

import re
from typing import Any

import pytest
from playwright.async_api import Page, expect

from tests.board_context import ADA, dialog, fresh_project, tally
from tests.board_helpers import (
    PAGES,
    SLOW,
    browser_page,
    crumbs,
    current_address,
    detail,
    go,
    item_address,
    item_loaded,
    links_of,
    made,
    no_panel_or_drawer,
    on_page,
    open_editor,
    open_item,
    part,
    record_row,
)
from tests.helpers import code_of, make_decision, make_note, ok, unique_word
from tests.helpers import number_of as number
from tests.world import Project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _browser(board: str, project: Project) -> Any:
    return browser_page(board, token=project.key, name=ADA)


def card(page: Page) -> Any:
    """The details of the epic, story or task whose page is on show."""
    return page.locator("#item-details .card-detail")


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
            await open_item(w, story)
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


async def test_acceptance_criteria_and_notes_are_shown_as_markdown_too(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Everywhere {word}")
        story = await made(client, "story", f"Place {word}", epic)
        rule = await ok(
            client,
            "create_rule",
            title=f"Rule {word}",
            node=story,
            body="A **bold** criterion",
        )
        decision = await make_decision(
            client,
            f"Decision {word}",
            text="Use **PDF**",
            reason="Because *it is stable*",
            nodes=[story],
        )
        plain = await make_note(
            client, f"A **note** about {word}", "The `team` call", nodes=[story]
        )
        async with _browser(board, project) as w:
            page = w.page
            # Each kind of record's own page reads its words as Markdown.
            await open_item(w, f"rule:{rule['id']}")
            await expect(page.locator(".rule-detail .body-text .markdown strong")).to_have_text(
                "bold", timeout=SLOW
            )

            await open_item(w, f"note:{decision['id']}")
            record = detail(page, f"note:{decision['id']}")
            await expect(record.locator(".record-text strong")).to_have_text("PDF", timeout=SLOW)
            await expect(record.locator(".record-why em")).to_have_text("it is stable")

            await open_item(w, f"note:{plain['id']}")
            record = detail(page, f"note:{plain['id']}")
            await expect(record.locator(".record-text strong")).to_have_text("note", timeout=SLOW)
            await expect(record.locator(".record-why code")).to_have_text("team")

            # In the table of the story's page the same entries are one line of plain words, with
            # all of the Markdown as the cell's title (and, whole, on the note's own page).
            await open_item(w, story)
            plain_row = record_row(page, f"note:{plain['id']}")
            await expect(plain_row).to_be_visible(timeout=SLOW)
            shown = " ".join((await plain_row.inner_text()).split())
            assert f"A note about {word}" in shown and "The team call" in shown, shown
            assert "**" not in shown and "`" not in shown, shown
            assert f"A **note** about {word}" in (
                await plain_row.locator(".cell-title .clamp").get_attribute("title") or ""
            )
            decision_row = record_row(page, f"note:{decision['id']}")
            shown = " ".join((await decision_row.inner_text()).split())
            assert f"Decision {word}" in shown and "Use PDF" in shown and "**" not in shown, shown
            assert "Use **PDF**" in (
                await decision_row.locator(".cell-title .clamp").get_attribute("title") or ""
            )
            assert w.problems == []


# -- references, titles, renames, missing numbers ------------------------------------------------


async def test_a_reference_shows_the_current_title_follows_a_rename_and_creates_no_link(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"References {word}")
        target = await made(client, "story", f"Target {word}", epic)
        rule = await ok(client, "create_rule", title=f"Ruled {word}", node=target)
        decision = await make_decision(client, f"Decided {word}")
        known = await make_note(client, f"Known {word}", "Observed")
        missing = f"story:{987654321}"
        # Only the five kinds are references: a decision or a fact has no reference of its own
        # now (it is a note), and neither a belief nor a node (`[[node:12]]` named any epic, story
        # or task once) is one, so each of those stays the words it is.
        body = (
            f"See [[{target}]], [[rule:{rule['id']}]], [[note:{decision['id']}]], "
            f"[[note:{known['id']}]], [[decision:{decision['id']}]], [[fact:{known['id']}]], "
            f"[[belief:3]], [[node:{number(target)}]] and [[{missing}]]."
        )
        mentioner = await made(client, "story", f"Mentioner {word}", epic, body=body)
        before_links = await links_of(client, target, mentioner)
        before_node = await ok(client, "get_node", node=mentioner)

        async with _browser(board, project) as w:
            page = w.page
            await open_item(w, mentioner, "roadmap")
            text = card(page).locator(".body-text .markdown")
            await expect(text.locator(".reference")).to_have_count(5, timeout=SLOW)

            def reference(ref: str) -> Any:
                return text.locator(f".reference[data-ref='{ref}']")

            await expect(reference(target)).to_contain_text(f"{code_of(target)} Target {word}")
            await expect(reference(f"rule:{rule['id']}")).to_contain_text(f"Ruled {word}")
            await expect(reference(f"note:{decision['id']}")).to_contain_text(f"Decided {word}")
            await expect(reference(f"note:{known['id']}")).to_contain_text(f"Known {word}")
            # Each is a link to the page of what it names, which says it came from this view.
            for ref in (
                target,
                f"rule:{rule['id']}",
                f"note:{decision['id']}",
                f"note:{known['id']}",
            ):
                assert await reference(ref).get_attribute("href") == item_address(ref, "roadmap")
            # A number nothing has is shown plainly as missing, and is not a link.
            gone = reference(missing)
            await expect(gone).to_have_class(re.compile(r"\bmissing\b"))
            await expect(gone).to_have_text(f"[[{missing}]] (missing)")
            assert await gone.get_attribute("href") is None
            # [[decision:N]], [[fact:N]], [[belief:3]] and [[node:N]] are not references: each stays
            # the text it is.
            shown = await text.inner_text()
            for plain_text in (
                f"[[decision:{decision['id']}]]",
                f"[[fact:{known['id']}]]",
                "[[belief:3]]",
                f"[[node:{number(target)}]]",
            ):
                assert plain_text in shown, plain_text
            for kind in ("decision", "fact", "belief", "node"):
                assert await text.locator(f".reference[data-kind={kind}]").count() == 0

            # A rename is followed by the reference without reloading the page.
            await ok(
                client,
                "update_node",
                node=target,
                expected_version=1,
                title=f"Renamed {word}",
            )
            await expect(reference(target)).to_contain_text(f"Renamed {word}", timeout=SLOW)
            await expect(reference(target)).not_to_contain_text("Target")
            await ok(
                client,
                "update_rule",
                rule_id=rule["id"],
                expected_version=1,
                title=f"Reworded {word}",
            )
            await expect(reference(f"rule:{rule['id']}")).to_contain_text(
                f"Reworded {word}", timeout=SLOW
            )

            # Following a reference goes to the page of the item it names, in the same view the
            # person was in, and Back returns to where the reference was.
            await reference(target).click()
            await item_loaded(page, target)
            await expect(page.locator("#item-title")).to_contain_text(
                f"Renamed {word}", timeout=SLOW
            )
            assert (await current_address(page))[1]["from"] == "roadmap"
            assert (await crumbs(page))[2] == "Roadmap"
            await no_panel_or_drawer(page)
            await page.go_back()
            await item_loaded(page, mentioner)
            await reference(f"rule:{rule['id']}").click()
            await item_loaded(page, f"rule:{rule['id']}")
            await expect(page.locator("#item-title")).to_contain_text(
                f"Reworded {word}", timeout=SLOW
            )
            await page.go_back()
            await item_loaded(page, mentioner)
            await reference(f"note:{decision['id']}").click()
            await item_loaded(page, f"note:{decision['id']}")
            await expect(page.locator("#item-title")).to_contain_text(f"Decided {word}")
            await page.go_back()
            await item_loaded(page, mentioner)
            await reference(f"note:{known['id']}").click()
            await item_loaded(page, f"note:{known['id']}")
            await expect(page.locator("#item-title")).to_contain_text(f"Known {word}")
            assert (await current_address(page))[1]["from"] == "roadmap"
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


async def test_mentioned_in_equals_find_mentions_on_every_page_and_in_the_list_and_follows_edits(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Backlinks {word}")
        target = await made(client, "story", f"Cited {word}", epic)
        rule = await ok(client, "create_rule", title=f"Cited rule {word}", node=target)
        known = await make_note(client, f"Cited note {word}", "Observed", nodes=[target])
        first = await made(client, "story", f"First {word}", epic, body=f"Refers to [[{target}]].")
        await ok(
            client,
            "update_rule",
            rule_id=rule["id"],
            expected_version=1,
            body=f"Also [[{target}]] and [[note:{known['id']}]].",
        )
        decision = await make_decision(
            client,
            f"Deciding {word}",
            text=f"Because of [[note:{known['id']}]]",
            reason=f"Like [[rule:{rule['id']}]]",
            nodes=[target],
        )

        async def mentions_say(page: Page, key: str, want: dict[str, Any]) -> None:
            """The page's "Mentioned in" for this record says what find_mentions says."""
            section = part(page, "mentions")
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
            # On the page of a story.
            await open_item(w, target)
            listed_now = await ok(client, "find_mentions", node=target)
            await mentions_say(page, "story", listed_now)
            assert first in sources(listed_now)
            assert f"rule:{rule['id']}" in sources(listed_now)

            # A new mention written by an agent shows up without reloading.
            second = await made(client, "task", f"Second {word}", first, body=f"[[{target}]]")
            section = part(page, "mentions")
            await expect(section.locator("li.mention")).to_have_count(
                len(listed_now["items"]) + 1, timeout=SLOW
            )
            await expect(section.locator(f"li.mention[data-source='{second}']")).to_be_visible()
            assert second in sources(await ok(client, "find_mentions", node=target))
            # What mentions it is a link to that page, in the same view; Back returns.
            await section.locator(f"li.mention[data-source='{first}'] a.mention-link").click()
            await item_loaded(page, first)
            assert (await current_address(page))[1]["from"] == "board"
            await page.go_back()
            await item_loaded(page, target)

            # On the page of an acceptance criterion and of two notes.
            for key, argument, ident in (
                (f"rule:{rule['id']}", "rule_id", rule["id"]),
                (f"note:{known['id']}", "note_id", known["id"]),
                (f"note:{decision['id']}", "note_id", decision["id"]),
            ):
                await open_item(w, key, "graph")
                want = await ok(client, "find_mentions", **{argument: ident})
                await mentions_say(page, key, want)
            by_rule = await ok(client, "find_mentions", rule_id=rule["id"])
            assert f"note:{decision['id']}" in sources(by_rule)
            by_note = await ok(client, "find_mentions", note_id=known["id"])
            assert {f"rule:{rule['id']}", f"note:{decision['id']}"} <= sources(by_note)
            # Nothing mentions the decision: its page says so, in the section's one line.
            await expect(part(page, "mentions").locator(".none")).to_have_text(
                "None yet.", timeout=SLOW
            )

            # In the criteria of Lists, a row's "Mentioned in" is closed until it is opened,
            # and then the same.
            await w.open(f"#/lists?view=requirements&epic={number(epic)}")
            row = page.locator("#unplaced-group, #requirement-groups").locator(
                f"li.gv-record[data-key='rule:{rule['id']}']"
            )
            details = row.locator("details.mentioned-in")
            await expect(details).to_be_visible(timeout=SLOW)
            await expect(details).not_to_have_attribute("open", "")
            await details.locator("summary").click()
            want = await ok(client, "find_mentions", rule_id=rule["id"])
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
        rule = await ok(client, "create_rule", title=f"Pickable rule {word}")
        known = await make_note(client, f"Pickable note {word}", "Observed")
        decision = await make_decision(client, f"Pickable decision {word}")
        before = await links_of(client, target, writer)
        async with _browser(board, project) as w:
            page = w.page
            await open_item(w, writer)
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
                f"[[note:{decision['id']}]]",
                f"[[note:{known['id']}]]",
            ):
                await expect(picker.locator(f"[data-ref='{ref}']")).to_have_count(1, timeout=SLOW)
            for never in ("[[belief", "[[node:", "[[decision:", "[[fact:"):
                assert await picker.locator(f"[data-ref^='{never}']").count() == 0
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
            await page.keyboard.type(f" and [[note:{known['id']}")
            await expect(picker.locator("[role=option]")).to_have_count(1, timeout=SLOW)
            await expect(picker.locator("[role=option]")).to_have_attribute(
                "data-ref", f"[[note:{known['id']}]]"
            )
            await page.keyboard.press("Escape")
            await expect(picker).to_be_hidden()
            assert (await box.input_value()).endswith(f"[[note:{known['id']}")

            # The same pick-list in the dialog that adds a note: it writes text, and nothing is
            # recorded until the dialog is sent.
            counts = await tally(client)
            await w.go(f"#/lists?view=notes&epic={number(epic)}")
            await page.locator("#add-note").click()
            text = dialog(page).locator("#add-note-text")
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


async def test_the_quick_switcher_opens_from_the_keyboard_on_every_page(
    server_url: str,
) -> None:
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", "Switch")
        decision = await make_decision(client, "Switchable")
        async with _browser(board, project) as w:
            page = w.page
            addresses = [
                *(f"#/{name}" for name in PAGES),
                "#/lists?view=notes",
                item_address(epic, "board"),
                item_address(f"note:{decision['id']}", "lists"),
            ]
            for address in addresses:
                await w.open(address)
                await page.wait_for_selector("#page", timeout=SLOW)
                await page.keyboard.press("Control+k")
                await expect(page.locator("#switcher")).to_be_visible(timeout=SLOW)
                await expect(page.locator("#switcher-input")).to_be_focused()
                await page.keyboard.press("Escape")
                await expect(page.locator("#switcher")).to_have_count(0)
                await no_panel_or_drawer(page)
            # The other way of writing the shortcut. There is no button for it: the search box
            # says the key instead, and the hint gives way once the box has focus.
            await page.keyboard.press("Meta+k")
            await expect(page.locator("#switcher")).to_be_visible()
            await page.keyboard.press("Escape")
            await expect(page.locator("#switcher")).to_have_count(0)
            await expect(page.locator("#switcher-open")).to_have_count(0)
            await expect(page.locator("#search-hint")).to_contain_text("or")
            await expect(page.locator("#search-hint kbd")).to_have_text(re.compile("⌘K|Ctrl K"))
            await page.locator("#global-search").focus()
            await expect(page.locator("#search-hint")).to_be_hidden()
            assert w.problems == []


async def test_the_quick_switcher_jumps_by_a_title_word_by_code_and_to_a_page(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Switching {word}")
        story = await made(client, "story", f"Findable {word}", epic)
        task = await made(client, "task", f"Doable {word}", story)
        rule = await ok(client, "create_rule", title=f"Switchable rule {word}", node=story)
        decision = await make_decision(client, f"Switchable decision {word}")
        known = await make_note(client, f"Switchable note {word}", "Observed")
        async with _browser(board, project) as w:
            page = w.page
            await w.open("#/roadmap")
            # By a word of the title: the item's page opens, in the view the person is on.
            await page.keyboard.press("Control+k")
            await page.keyboard.type(f"findable {word}")
            first = page.locator("#switcher [role=option]").first
            await expect(first).to_have_attribute("data-ref", story, timeout=SLOW)
            await page.keyboard.press("Enter")
            await expect(page.locator("#switcher")).to_have_count(0)
            await item_loaded(page, story)
            await expect(card(page)).to_contain_text(f"Findable {word}", timeout=SLOW)
            assert (await current_address(page))[1] == {"item": story, "from": "roadmap"}
            assert (await crumbs(page))[2] == "Roadmap"
            await no_panel_or_drawer(page)

            # By code, as the page writes it.
            await w.go("#/board")
            await page.keyboard.press("Control+k")
            await page.keyboard.type(code_of(epic))
            await expect(page.locator("#switcher [role=option]").first).to_have_attribute(
                "data-ref", epic, timeout=SLOW
            )
            await page.keyboard.press("Enter")
            await item_loaded(page, epic)
            await expect(card(page)).to_contain_text(f"Switching {word}", timeout=SLOW)
            assert (await current_address(page))[1]["from"] == "board"

            # Epics, stories, tasks, criteria and notes are numbered each on its own, so a
            # bare number finds every kind that has it: in a project of its own the first epic,
            # story, task, criterion and note are all number 1, and the second note is number 2.
            assert [number(ref) for ref in (epic, story, task)] == [1, 1, 1]
            assert [rule["id"], decision["id"], known["id"]] == [1, 1, 2]
            await w.go("#/board")
            await page.keyboard.press("Control+k")
            await page.keyboard.type("1")
            options = page.locator("#switcher [role=option]")
            await expect(options).to_have_count(5, timeout=SLOW)
            assert await options.evaluate_all("els => els.map(e => e.dataset.ref)") == [
                epic,
                story,
                task,
                f"rule:{rule['id']}",
                f"note:{decision['id']}",
            ]
            await page.keyboard.press("Escape")
            await expect(page.locator("#switcher")).to_have_count(0)

            # By kind and number: a criterion, a note. Each opens its own page, in the view the
            # person was on.
            for typed, key in (
                (f"criterion {rule['id']}", f"rule:{rule['id']}"),
                (f"note {decision['id']}", f"note:{decision['id']}"),
                (f"note {known['id']}", f"note:{known['id']}"),
            ):
                await w.go("#/lists?view=notes")
                await page.keyboard.press("Control+k")
                await page.keyboard.type(typed)
                await expect(page.locator("#switcher [role=option]")).to_have_count(1, timeout=SLOW)
                await page.keyboard.press("Enter")
                await item_loaded(page, key)
                assert (await current_address(page))[1] == {"item": key, "from": "lists"}
                assert (await crumbs(page))[2] == "Lists"
                await no_panel_or_drawer(page)

            # To a view and to the pages of Search, Activity and the coverage report, with the
            # keyboard: each is a page of its own.
            await w.go("#/board")
            await page.keyboard.press("Control+k")
            await page.keyboard.type("graph")
            await expect(page.locator("#switcher [data-page=graph]")).to_be_visible()
            await page.keyboard.press("Enter")
            await expect(page.locator("#graph-explore")).to_be_visible(timeout=SLOW)
            assert "#/graph" in await page.evaluate("location.hash")
            for name in ("activity", "search", "coverage"):
                await page.keyboard.press("Control+k")
                await page.keyboard.type(name)
                await expect(page.locator(f"#switcher [data-page={name}]")).to_be_visible()
                await page.keyboard.press("Enter")
                await on_page(page, name)
                await no_panel_or_drawer(page)

            # Nothing matching says so, and Enter then goes nowhere.
            await page.keyboard.press("Control+k")
            await page.keyboard.type(f"zz{word}")
            await expect(page.locator("#switcher")).to_contain_text("Nothing matches")
            await page.keyboard.press("Enter")
            await expect(page.locator("#switcher")).to_be_visible()
            assert w.problems == []


# -- the graph's mentions -------------------------------------------------------------------------


async def test_the_graph_draws_mentions_between_work_when_asked_and_names_them_in_around(
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
            title=f"Rule {word}",
            node=cited,
            body=f"Mentions [[{citing}]]",
        )
        decision = await make_decision(
            client, f"Decision {word}", text=f"After [[rule:{rule['id']}]]", nodes=[cited]
        )
        await make_note(client, f"Note {word}", f"See [[note:{decision['id']}]]", nodes=[cited])
        before = await links_of(client, cited, citing)
        everything = await ok(client, "find_mentions", limit=200)
        # Every reference to something that exists: five of the six name something, and the one
        # that points at nothing is not drawn anywhere. Only the references between work are
        # lines of Structure (an epic, a story or a task is a dot there, a criterion or a note
        # is not).
        named = [m for m in everything["items"] if m["target"]["exists"]]
        assert len(named) == 4 and len(everything["items"]) == 5
        between_work = {
            (m["source"]["ref"], m["target"]["ref"])
            for m in named
            if m["source"]["ref"].split(":")[0] in ("epic", "story", "task")
            and m["target"]["ref"].split(":")[0] in ("epic", "story", "task")
        }
        assert between_work == {(citing, cited)}
        async with _browser(board, project) as w:
            page = w.page
            lines = "svg.graph-st-svg .graph-st-link.t-mention"
            await go(page, board, "#/graph")
            await expect(page.locator("svg.graph-st-svg")).to_be_visible(timeout=SLOW)
            assert await page.locator(lines).count() == 0
            await page.locator("button[data-choice=links]").click()
            await expect(page.locator(lines)).to_have_count(len(between_work), timeout=SLOW)
            assert "links=1" in await page.evaluate("location.hash")
            await page.locator("button[data-choice=links]").click()
            await expect(page.locator(lines)).to_have_count(0, timeout=SLOW)

            # Around draws what names the item: the story that cites it is among what is related
            # to it.
            await w.open(f"#/graph?tab=around&focus={cited}")
            around = page.locator(f".graph-around[data-focus='{cited}']")
            await expect(around).to_be_visible(timeout=SLOW)
            await expect(
                around.locator(f"section[data-group=sim] button.graph-node[data-ref='{citing}']")
            ).to_have_count(1, timeout=SLOW)
            assert w.problems == []
        assert await links_of(client, cited, citing) == before
