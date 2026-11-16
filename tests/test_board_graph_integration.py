"""M16's Graph against the running containers: the Work, Context and Everything presets, and the Map
and List presentations of one scope, driven in a real headless browser and checked against a real
MCP client (see ./check.sh). The same records and the same lines must be in the map, in the list and
in what the tools say. The Graph tests work in a project of their own, so that what the context list
always shows (requirements in no story, project-wide decisions, records about nothing) is only what
the test made. The "everything above the limit" test needs a graph of a known size, so it runs on a
second, empty copy of Vrum (tests/second_stack.py) that is made for the test and removed after
it."""

import re
from collections.abc import Iterator
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_context import (
    ADA,
    REMOVED_WORDS,
    check_same_relationships,
    context_world,
    dialog,
    dot,
    dot_keys,
    drawn_as,
    edge,
    fresh_project,
    graph_open,
    hash_params,
    list_as,
    listed,
    panel,
    server_edges,
    show,
    svg,
    whole,
)
from tests.board_helpers import SLOW, Watched, board_url, browser_page, made
from tests.helpers import all_nodes, make_decision, make_fact, ok, reindexed, unique_word
from tests.second_stack import SecondStack
from tests.world import Project

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _browser(board: str, project: Project) -> Any:
    return browser_page(board, token=project.key, name=ADA)


def keys(ids: dict[str, int], kind: str, *names: str) -> set[str]:
    return {f"{kind}:{ids[name]}" for name in names}


def lines(ids: dict[str, int], kind: str, *pairs: tuple[str, str]) -> set[tuple[str, str, str]]:
    """Lines of one kind between records named by their short names such as ('s1', 'r1') for the
    record kinds the world's names tell apart (r rules, d decisions, f facts, the rest work)."""

    def key(name: str) -> str:
        what = {"r": "rule", "d": "decision", "f": "fact"}.get(name[0], "node")
        return f"{what}:{ids[name]}"

    return {(kind, key(a), key(b)) for a, b in pairs}


async def pressed(page: Page) -> tuple[str, str]:
    """The choice each group of buttons says it has made, by aria-pressed."""
    out = []
    for group in ("#graph-views", "#graph-presentations"):
        on = await page.locator(f"{group} button[aria-pressed=true]").evaluate_all(
            "els => els.map(e => e.dataset.value)"
        )
        assert len(on) == 1, on
        out.append(on[0])
    return out[0], out[1]


async def same_as_the_boards_in(w: Watched, key: str) -> None:
    """The panel, as the Graph opened it, is what the Board shows for the same record: the same
    words and the same parts, read again from the server in a page loaded afresh."""
    page = w.page
    await page.wait_for_load_state("networkidle")
    from_graph = await panel(page).inner_text()
    sections = (
        await panel(page).locator("[data-part]").evaluate_all("els => els.map(e => e.dataset.part)")
    )
    await page.goto(f"{w.board}?again={unique_word()}#/board?sel={key}")
    await expect(page.locator("#main-nav")).to_be_visible(timeout=SLOW)
    await expect(panel(page).locator("[data-part]").first).to_be_visible(timeout=SLOW)
    await page.wait_for_load_state("networkidle")
    assert await panel(page).inner_text() == from_graph
    assert (
        await panel(page).locator("[data-part]").evaluate_all("els => els.map(e => e.dataset.part)")
        == sections
    )
    await page.goto(f"{w.board}?again={unique_word()}#/graph?view=everything&as=list")


# -- the presets and the presentations -------------------------------------------------------------


class Expect:
    """What the Graph shows for the world of context_world, scoped to epic E: the dots of each
    kind, the work that context is joined to, and the lines."""

    def __init__(self, ids: dict[str, int]) -> None:
        self.work = keys(ids, "node", "E", "s1", "s2", "t1", "t2", "t3")
        self.records = (
            keys(ids, "rule", "r1", "r2", "ru")
            | keys(ids, "decision", "d1", "dw", "d_new")
            | keys(ids, "fact", "f1", "f2", "f3", "f_new")
        )
        self.joined = keys(ids, "node", "s1", "s2")
        self.work_lines = (
            lines(ids, "hierarchy", ("E", "s1"), ("E", "s2"), ("s1", "t1"), ("s1", "t2"))
            | lines(ids, "hierarchy", ("s2", "t3"))
            | lines(ids, "needs_first", ("t2", "t1"))
            | lines(ids, "relates_to", ("s1", "s2"))
        )
        self.context_lines = lines(ids, "placed", ("r1", "s1"), ("r2", "s2")) | lines(
            ids,
            "concerns",
            ("d1", "s1"),
            ("d1", "r1"),
            ("d_new", "s2"),
            ("f1", "s1"),
            ("f1", "r1"),
            ("f2", "d1"),
            ("f_new", "s2"),
        )
        # Per preset: the dots of the map, its lines, and the rows of the list.
        self.by_view = {
            "work": (self.work, self.work_lines, self.work),
            "context": (self.records | self.joined, self.context_lines, self.records),
            "everything": (
                self.work | self.records,
                self.work_lines | self.context_lines,
                self.work | self.records,
            ),
        }


async def test_each_preset_shows_work_context_or_both_with_the_right_lines_on_the_map(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        ids = await context_world(client, word)
        want = Expect(ids)
        work, records, joined = want.work, want.records, want.joined
        work_lines, context_lines = want.work_lines, want.context_lines
        async with _browser(board, project) as w:
            page = w.page
            # No choice in the address: Work, as a map.
            await graph_open(w, f"#/graph?epic={ids['E']}")
            assert await pressed(page) == ("work", "map")
            await drawn_as(page, work, work_lines)
            assert await svg(page).locator(".dot:not([data-kind='node'])").count() == 0

            # Context: requirements, decisions and facts that are in force, and the work they are
            # joined to; no hierarchy, no dependency, nothing that has ended, nothing of epic E2.
            await show(page, view="context")
            assert await pressed(page) == ("context", "map")
            assert "view=context" in await page.evaluate("location.hash")
            await drawn_as(page, records | joined, context_lines)
            for name, status in (("r2", "settled"), ("d1", "active"), ("f_new", "active")):
                kind = {"r": "rule", "d": "decision", "f": "fact"}[name[0]]
                assert await dot(page, f"{kind}:{ids[name]}").get_attribute("data-status") == status
            assert await dot(page, f"decision:{ids['d1']}").get_attribute("data-confirmation") == (
                "confirmed"
            )
            assert await dot(page, f"fact:{ids['f2']}").get_attribute("data-confirmation") == (
                "unconfirmed"
            )
            assert await dot(page, f"node:{ids['s1']}").get_attribute("data-kind") == "node"
            assert await dot(page, f"rule:{ids['ru']}").get_attribute("data-kind") == "rule"
            await expect(dot(page, f"decision:{ids['dw']}")).to_have_count(1)

            # Everything is both.
            await show(page, view="everything")
            assert await pressed(page) == ("everything", "map")
            await drawn_as(page, work | records, work_lines | context_lines)
            assert "view=everything" in await page.evaluate("location.hash")

            # What has ended is left out until asked for, and then the lines say what replaced what.
            await page.locator("[name=ended]").check()
            assert "ended=1" in await page.evaluate("location.hash")
            ended = (
                keys(ids, "rule", "rd")
                | keys(ids, "decision", "d_old", "d_gone")
                | keys(ids, "fact", "f_old", "f_gone")
            )
            await expect(svg(page)).to_have_attribute(
                "data-dots", str(len(work | records | ended)), timeout=SLOW
            )
            assert await dot_keys(page) == sorted(work | records | ended)
            for key in ended:
                assert await dot(page, key).get_attribute("data-status") in {
                    "dropped",
                    "superseded",
                    "withdrawn",
                }
                await expect(dot(page, key)).to_have_class(re.compile(r"\bended\b"))
            await expect(
                edge(page, "replaces", f"decision:{ids['d_new']}", f"decision:{ids['d_old']}")
            ).to_have_count(1)
            await expect(
                edge(page, "replaces", f"fact:{ids['f_new']}", f"fact:{ids['f_old']}")
            ).to_have_count(1)
            # Every line the map draws is a relationship the tools report, and the other way round.
            shown = work | records | ended
            expected = {
                line for line in await server_edges(client) if line[1] in shown and line[2] in shown
            }
            await drawn_as(page, shown, expected)
            assert w.problems == [], w.problems


async def test_the_map_and_the_list_hold_the_same_records_and_relationships_in_every_preset(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        ids = await context_world(client, word)
        want = Expect(ids)
        links = await server_edges(client)
        assert len(links) >= 25  # the world is not trivially small
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, f"#/graph?epic={ids['E']}")
            for view, (dots, map_lines, rows) in want.by_view.items():
                await show(page, view=view, presentation="map")
                await drawn_as(page, dots, map_lines)
                # The list holds the same records, and says the same lines at both of their ends.
                await check_same_relationships(page, rows)
                await show(page, presentation="list")
                await list_as(page, rows)
                # Each row is what the tools say: its id, and for context its lifecycle and
                # confirmation.
                for key in rows:
                    kind, ident = key.split(":")
                    await expect(listed(page, key)).to_have_attribute("data-id", ident)
                    if kind == "node":
                        continue
                    tool = {"rule": "get_rule", "decision": "get_decision", "fact": "get_fact"}[
                        kind
                    ]
                    record = await ok(client, tool, **{f"{kind}_id": int(ident)})
                    row = listed(page, key)
                    await expect(row).to_have_attribute("data-status", record["status"])
                    if kind != "rule":
                        await expect(row).to_have_attribute(
                            "data-confirmation", record["confirmation"]
                        )
                await show(page, presentation="map")
            # The counts of the list's sections are what the tools count: every decision and fact
            # in force, apart from the ones about the other epic.
            await show(page, view="context", presentation="list")
            for plural, tool, active, other in (
                ("decisions", "list_decisions", {"d1", "dw", "d_new"}, "d9"),
                ("facts", "list_facts", {"f1", "f2", "f3", "f_new"}, "f9"),
            ):
                found = {r["id"] for r in await whole(client, tool, status="active")}
                assert found - {ids[other]} == {ids[name] for name in active}
                everything = {r["id"] for r in await whole(client, tool)} - {ids[other]}
                await expect(page.locator(f"#ctx-{plural}-count")).to_have_attribute(
                    "data-shown", str(len(active)), timeout=SLOW
                )
                await expect(page.locator(f"#ctx-{plural}-count")).to_have_attribute(
                    "data-total", str(len(everything))
                )
                assert len(everything) > len(active)  # the ended ones are in the scope, not shown
            assert w.problems == [], w.problems


# -- the choice and the scope survive switching; the address reproduces the state ---------


async def test_the_chosen_record_and_the_scope_survive_switching_and_the_address_reproduces_them(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        ids = await context_world(client, word)
        want = Expect(ids)
        epic, story = ids["E"], ids["s1"]
        # A work filter (stories only) narrows the work; the context that is joined to it stays.
        stories = keys(ids, "node", "E", "s1", "s2")
        filtered = {
            "work": (stories, lines(ids, "hierarchy", ("E", "s1"), ("E", "s2"))
                     | lines(ids, "relates_to", ("s1", "s2")), stories),
            "context": want.by_view["context"],
            "everything": (
                stories | want.records,
                lines(ids, "hierarchy", ("E", "s1"), ("E", "s2"))
                | lines(ids, "relates_to", ("s1", "s2"))
                | want.context_lines,
                stories | want.records,
            ),
        }  # fmt: skip
        address = f"#/graph?view=everything&epic={epic}&q=kind:story&sel=node:{story}"
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, address)
            await expect(panel(page).locator(f".card-detail[data-id='{story}']")).to_be_visible(
                timeout=SLOW
            )
            # Mark the panel's own element: it must be the very same one after every switch.
            await panel(page).locator(".card-detail").evaluate("e => { e.dataset.mark = 'kept'; }")
            steps = [
                ("context", "map"),
                ("context", "list"),
                ("everything", "list"),
                ("work", "list"),
                ("work", "map"),
                ("everything", "map"),
            ]
            for view, presentation in steps:
                await show(page, view=view, presentation=presentation)
                dots, map_lines, rows = filtered[view]
                if presentation == "map":
                    await drawn_as(page, dots, map_lines)
                    chosen = dot(page, f"node:{story}")
                    await expect(chosen).to_have_class(re.compile(r"\bchosen\b"))
                    await expect(chosen).to_have_attribute("aria-pressed", "true")
                else:
                    await list_as(page, rows)
                    if view != "context":
                        await expect(listed(page, f"node:{story}")).to_have_attribute(
                            "aria-current", "true"
                        )
                params = await hash_params(page)
                assert (params["epic"], params["q"], params["sel"]) == (
                    str(epic),
                    "kind:story",
                    f"node:{story}",
                )
                assert (params["view"], params.get("as", "map")) == (view, presentation)
                assert await pressed(page) == (view, presentation)
                assert await page.locator("#graph-epic").input_value() == str(epic)
                assert await page.locator("#filter").input_value() == "kind:story"
                kept = panel(page).locator(".card-detail[data-mark=kept]")
                await expect(kept).to_have_count(1)
                await expect(kept).to_have_attribute("data-id", str(story))

            # Choosing another record, from the list, keeps everything but the choice, and the
            # map then marks that record.
            await show(page, view="everything", presentation="list")
            fact = f"fact:{ids['f1']}"
            await listed(page, fact).locator(".record-title").click()
            await expect(
                panel(page).locator(f".record-detail[data-record='{fact}']")
            ).to_be_visible(timeout=SLOW)
            params = await hash_params(page)
            assert (params["epic"], params["q"], params["sel"]) == (
                str(epic),
                "kind:story",
                fact,
            )
            await expect(listed(page, fact)).to_have_attribute("aria-current", "true")
            await show(page, presentation="map")
            await expect(dot(page, fact)).to_have_class(re.compile(r"\bchosen\b"))
            await expect(dot(page, f"node:{story}")).not_to_have_class(re.compile(r"\bchosen\b"))

            # The address alone brings the whole state back: reloaded, and in a page of its own.
            await show(page, view="context", presentation="list")
            address = await page.evaluate("location.hash")
            await page.reload()
            await expect(page.locator("#graph-list")).to_be_visible(timeout=SLOW)
            assert await pressed(page) == ("context", "list")
            assert await page.locator("#graph-epic").input_value() == str(epic)
            assert await page.locator("#filter").input_value() == "kind:story"
            await expect(
                panel(page).locator(f".record-detail[data-record='{fact}']")
            ).to_be_visible(timeout=SLOW)
            await list_as(page, filtered["context"][2])
            await expect(listed(page, fact)).to_have_attribute("aria-current", "true")
            other = await page.context.new_page()
            await other.goto(f"{board}{address}")
            await expect(other.locator("#graph-list")).to_be_visible(timeout=SLOW)
            assert await other.evaluate("location.hash") == address
            await list_as(other, filtered["context"][2])
            await expect(other.locator("#graph-view-context")).to_have_attribute(
                "aria-pressed", "true"
            )
            await other.close()

            # Around the chosen record, one step, in either presentation: the same records.
            await w.go(f"#/graph?view=everything&epic={epic}&sel={fact}")
            await page.locator("#graph-reach").select_option("1")
            around = {fact, f"node:{story}", f"rule:{ids['r1']}"}
            await expect(page.locator("#graph-page")).to_have_attribute("data-as", "map")
            await drawn_as(
                page,
                around,
                lines(ids, "concerns", ("f1", "s1"), ("f1", "r1"))
                | lines(ids, "placed", ("r1", "s1")),
            )
            assert (await hash_params(page))["scope"] == "local"
            await show(page, presentation="list")
            await list_as(page, around)
            assert (await hash_params(page))["scope"] == "local"
            assert w.problems == [], w.problems


async def test_the_presets_and_presentations_are_buttons_that_work_from_the_keyboard(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        ids = await context_world(client, word)
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, f"#/graph?epic={ids['E']}")
            for group in ("#graph-views", "#graph-presentations"):
                assert await page.locator(group).get_attribute("role") == "group"
                assert await page.locator(group).get_attribute("aria-label")
                for button in await page.locator(f"{group} button").all():
                    assert await button.get_attribute("aria-pressed") in {"true", "false"}
            assert await pressed(page) == ("work", "map")

            # Tab moves from one button to the next; Enter and Space choose.
            await page.locator("#graph-view-work").focus()
            await page.keyboard.press("Tab")
            await expect(page.locator("#graph-view-context")).to_be_focused()
            await page.keyboard.press("Enter")
            await expect(page.locator("#graph-view-context")).to_have_attribute(
                "aria-pressed", "true", timeout=SLOW
            )
            await expect(page.locator("#graph-view-work")).to_have_attribute(
                "aria-pressed", "false"
            )
            await page.keyboard.press("Tab")
            await expect(page.locator("#graph-view-everything")).to_be_focused()
            await page.keyboard.press("Space")
            await expect(page.locator("#graph-view-everything")).to_have_attribute(
                "aria-pressed", "true", timeout=SLOW
            )
            assert (await hash_params(page))["view"] == "everything"
            assert await pressed(page) == ("everything", "map")
            # The epic choice sits between the presets and the presentations, as it reads.
            await page.keyboard.press("Tab")
            await expect(page.locator("#graph-epic")).to_be_focused()
            await page.keyboard.press("Tab")
            await expect(page.locator("#graph-as-map")).to_be_focused()
            await page.keyboard.press("Tab")
            await expect(page.locator("#graph-as-list")).to_be_focused()
            await page.keyboard.press("Enter")
            await expect(page.locator("#graph-list")).to_be_visible(timeout=SLOW)
            assert (await hash_params(page))["as"] == "list"
            assert await pressed(page) == ("everything", "list")
            # Where the keyboard is stays where it was: on the button just used.
            await expect(page.locator("#graph-as-list")).to_be_focused()
            await list_as(page, Expect(ids).by_view["everything"][2])
            assert w.problems == [], w.problems


# -- no taxonomy that is gone -------------------------------------------------------------


async def test_no_belief_principle_or_memory_taxonomy_appears_in_any_preset_or_presentation(
    server_url: str,
) -> None:
    word = unique_word()
    kinds = {"node", "rule", "decision", "fact"}
    types = {"epic", "story", "task", "rule", "decision", "fact"}
    async with fresh_project(server_url) as (client, board, project):
        ids = await context_world(client, word)
        gone = re.compile("belief|principle|memor|stale|promot", re.I)
        names = [tool.name for tool in (await client.list_tools()).tools]
        assert not [name for name in names if gone.search(name)], names
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, f"#/graph?epic={ids['E']}&ended=1")
            for view in ("work", "context", "everything"):
                for presentation in ("map", "list"):
                    await show(page, view=view, presentation=presentation)
                    await page.wait_for_load_state("networkidle")
                    text = await page.locator("body").text_content() or ""
                    assert not REMOVED_WORDS.search(text), (
                        view,
                        presentation,
                        REMOVED_WORDS.findall(text),
                    )
                    found: list[list[str]] = await page.locator(
                        "#graph-page [data-kind]"
                    ).evaluate_all("els => els.map(e => [e.dataset.kind, e.dataset.type || ''])")
                    assert {k for k, _ in found} <= kinds, found
                    assert {t for _, t in found if t} <= types, found
            switches = await page.locator("[name^=kind-]").evaluate_all(
                "els => els.map(e => e.name)"
            )
            assert sorted(switches) == sorted(f"kind-{kind}" for kind in types)
            # Nor in what opens from it: the detail of each kind of record, and the dialogs.
            for key in (
                f"node:{ids['s1']}",
                f"rule:{ids['r1']}",
                f"decision:{ids['d1']}",
                f"fact:{ids['f1']}",
                f"fact:{ids['f_old']}",
            ):
                await w.go(f"#/graph?view=context&as=list&epic={ids['E']}&sel={key}")
                await expect(panel(page).locator("h2").first).to_be_visible(timeout=SLOW)
                await page.wait_for_load_state("networkidle")
                shown = await panel(page).text_content() or ""
                assert not REMOVED_WORDS.search(shown), (key, REMOVED_WORDS.findall(shown))
            for button in ("#record-fact", "#record-decision", "#new-requirement"):
                await page.locator(button).click()
                await expect(dialog(page)).to_be_visible()
                said = await dialog(page).text_content() or ""
                assert not REMOVED_WORDS.search(said), (button, REMOVED_WORDS.findall(said))
                await page.keyboard.press("Escape")
                await expect(dialog(page)).to_have_count(0)
            assert w.problems == [], w.problems


# -- a draft in the detail panel is not lost ----------------------------------------------


async def test_a_draft_in_the_detail_panel_survives_switching_presentation_and_opening_a_drawer(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        ids = await context_world(client, word)
        story = ids["s1"]
        before = await ok(client, "get_node", node_id=story)
        draft_title, draft_body = f"Draft title {word}", f"Draft details {word}"
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, f"#/graph?epic={ids['E']}&sel=node:{story}")
            card = panel(page).locator(".card-detail")
            await expect(card).to_be_visible(timeout=SLOW)
            await card.evaluate("e => { e.dataset.mark = 'kept'; }")
            await page.locator("#edit-title").fill(draft_title)
            await page.locator("#edit-body").fill(draft_body)

            async def draft_is_kept(where: str) -> None:
                assert await page.locator("#edit-title").input_value() == draft_title, where
                assert await page.locator("#edit-body").input_value() == draft_body, where
                await expect(panel(page).locator(".card-detail[data-mark=kept]")).to_have_count(1)
                now = await ok(client, "get_node", node_id=story)
                assert (now["title"], now["body"], now["version"]) == (
                    before["title"],
                    before["body"],
                    before["version"],
                ), where

            # Another preset, the list, a third preset.
            await show(page, view="context")
            await draft_is_kept("context map")
            await show(page, presentation="list")
            await draft_is_kept("context list")
            await show(page, view="everything")
            await draft_is_kept("everything list")
            # The search drawer, with a search of its own; then the activity drawer.
            await page.locator("#global-search").fill(word)
            await page.locator("#global-search-go").click()
            await expect(page.locator("#drawer[data-drawer=search]")).to_be_visible(timeout=SLOW)
            await expect(page.locator("#search-box")).to_have_value(word)
            await draft_is_kept("search drawer")
            await page.locator("#drawer-close").click()
            await expect(page.locator("#drawer")).to_be_hidden()
            await draft_is_kept("search drawer closed")
            await page.locator("#activity-open").click()
            await expect(page.locator("#drawer[data-drawer=activity]")).to_be_visible(timeout=SLOW)
            await draft_is_kept("activity drawer")
            await show(page, presentation="map")
            await draft_is_kept("activity drawer and the map")
            await page.locator("#drawer-close").click()
            # To the Board and back: the panel is the same one, with the same draft.
            await page.locator("#main-nav a[data-page=board]").click()
            await expect(page.locator("#main-nav a[data-page=board]")).to_have_attribute(
                "aria-current", "page", timeout=SLOW
            )
            await draft_is_kept("board")
            await page.locator("#main-nav a[data-page=graph]").click()
            await expect(page.locator("#graph-page")).to_be_visible(timeout=SLOW)
            await draft_is_kept("graph again")

            # An agent changes the story meanwhile: the draft stays, and the panel offers the
            # change instead of drawing over what was typed.
            await ok(
                client,
                "update_node",
                actor="agent-1",
                node_id=story,
                expected_version=before["version"],
                priority="high",
            )
            await expect(panel(page).locator(".changed")).to_be_visible(timeout=SLOW)
            assert await page.locator("#edit-title").input_value() == draft_title
            assert await page.locator("#edit-body").input_value() == draft_body

            # Saved at last, at the version it was typed against, the draft is refused as stale
            # in the server's words, and what was typed is still there.
            await page.get_by_role("button", name="Save changes").click()
            problem = panel(page).locator(".edit .problem")
            await expect(problem).to_contain_text("version", timeout=SLOW)
            assert await page.locator("#edit-title").input_value() == draft_title
            assert (await ok(client, "get_node", node_id=story))["title"] == before["title"]
            await panel(page).locator("button.load-current").first.click()
            await expect(page.locator("#edit-title")).to_have_value(before["title"], timeout=SLOW)
            assert w.problems == [], w.problems


# -- following a connection, a backlink or a reference to the ordinary detail panel --------


async def test_a_connection_a_backlink_or_a_reference_opens_the_ordinary_detail_panel(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Followed {word}")
        story = await made(client, "story", f"Referred story {word}", epic)
        await made(client, "task", f"A task {word}", story)
        decision = await make_decision(client, f"Referred decision {word}", node_ids=[story])
        fact = await make_fact(
            client,
            f"Refers to [[node:{story}]] and [[decision:{decision['id']}]] {word}",
            node_ids=[story],
            decision_ids=[decision["id"]],
        )
        mentions = await ok(client, "find_mentions", decision_id=decision["id"])
        sources = {(m["source"]["item_type"], m["source"]["id"]) for m in mentions["items"]}
        assert ("fact", fact["id"]) in sources
        fact_key, decision_key = f"fact:{fact['id']}", f"decision:{decision['id']}"
        await reindexed(client)

        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=everything&as=list&epic={epic}")

            # A backlink: the decision's row says what mentions it, as find_mentions does, and a
            # click on one opens that record in the panel, with the Graph left as it was.
            row = listed(page, decision_key)
            await row.locator("details.mentioned-in summary").click()
            items = row.locator("details.mentioned-in li.mention")
            await expect(items).to_have_count(len(mentions["items"]), timeout=SLOW)
            shown = await items.evaluate_all("els => els.map(e => e.dataset.source)")
            assert sorted(shown) == sorted(f"{kind}:{ident}" for kind, ident in sources)
            await row.locator(f"li.mention[data-source='fact:{fact['id']}'] a.mention-link").click()
            detail = panel(page).locator(f".record-detail[data-record='{fact_key}']")
            await expect(detail).to_be_visible(timeout=SLOW)
            params = await hash_params(page)
            assert (params["sel"], params["view"], params["as"], params["epic"]) == (
                fact_key,
                "everything",
                "list",
                str(epic),
            )
            await expect(listed(page, fact_key)).to_have_attribute("aria-current", "true")
            await same_as_the_boards_in(w, fact_key)

            # A reference in the record's own words opens the item it names.
            await w.go(f"#/graph?view=everything&as=list&epic={epic}&sel={fact_key}")
            await expect(detail).to_be_visible(timeout=SLOW)
            await detail.locator(
                f".record-text .reference[data-kind=node][data-id='{story}']"
            ).click()
            await expect(panel(page).locator(f".card-detail[data-id='{story}']")).to_be_visible(
                timeout=SLOW
            )
            assert (await hash_params(page))["sel"] == f"node:{story}"
            assert (await hash_params(page))["view"] == "everything"
            await expect(listed(page, f"node:{story}")).to_have_attribute("aria-current", "true")
            await same_as_the_boards_in(w, f"node:{story}")

            # A connection in the list: from the fact's row to the story it concerns.
            await w.go(f"#/graph?view=everything&as=list&epic={epic}")
            await expect(listed(page, fact_key)).to_be_visible(timeout=SLOW)
            await listed(page, fact_key).locator("details.gv-connections summary").click()
            link = listed(page, fact_key).locator(
                f"details.gv-connections li[data-other='node:{story}'] button.gv-link"
            )
            await expect(link).to_have_text(re.compile(rf"#{story} Referred story"))
            await link.click()
            await expect(panel(page).locator(f".card-detail[data-id='{story}']")).to_be_visible(
                timeout=SLOW
            )
            await expect(listed(page, f"node:{story}")).to_have_attribute("aria-current", "true")

            # On the map: a dot, then what it is joined to, then a link in the record's panel.
            await w.go(f"#/graph?view=everything&epic={epic}&sel={fact_key}")
            await expect(dot(page, fact_key)).to_have_class(re.compile(r"\bchosen\b"), timeout=SLOW)
            await expect(
                panel(page).locator(f".record-detail[data-record='{fact_key}']")
            ).to_be_visible()
            near = page.locator("#graph-neighbours button[data-key]")
            joined = await near.evaluate_all(
                "els => els.map(e => [e.dataset.key, e.dataset.relation])"
            )
            assert sorted(joined) == sorted(
                [[f"node:{story}", "concerns"], [decision_key, "concerns"]]
            )
            await page.locator(f"#graph-neighbours button[data-key='{decision_key}']").click()
            await expect(
                panel(page).locator(f".record-detail[data-record='{decision_key}']")
            ).to_be_visible(timeout=SLOW)
            await expect(dot(page, decision_key)).to_have_class(re.compile(r"\bchosen\b"))
            await (
                panel(page).locator(f"[data-part=concerns] a.item-link[data-id='{story}']").click()
            )
            await expect(panel(page).locator(f".card-detail[data-id='{story}']")).to_be_visible(
                timeout=SLOW
            )
            await expect(dot(page, f"node:{story}")).to_have_class(re.compile(r"\bchosen\b"))
            assert w.problems == [], w.problems


# -- the map: filters in the address, choosing a dot, what is around it, the keyboard -------


async def test_a_filter_by_kind_status_and_line_is_kept_in_the_address(server_url: str) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Filters {word}")
        story = await made(client, "story", f"Story {word}", epic)
        done = await made(client, "task", f"Done task {word}", story)
        open_ = await made(client, "task", f"Open task {word}", story)
        await ok(client, "update_node", actor="a", node_id=done, expected_version=1, status="done")
        await ok(
            client, "add_link", actor="a", source_id=open_, target_id=done, link_type="relates_to"
        )
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, f"#/graph?epic={epic}")
            await expect(dot(page, f"node:{done}")).to_have_count(1)
            await page.locator("#filters summary").click()
            await page.locator("[name=status-done]").uncheck()
            await expect(dot(page, f"node:{done}")).to_have_count(0)
            assert "hide_status=done" in page.url
            await page.locator("[name=kind-task]").uncheck()
            await expect(svg(page).locator(".dot[data-type='task']")).to_have_count(0)
            await page.locator("[name=kind-task]").check()
            await page.locator("[name=link-contains]").uncheck()
            await expect(svg(page).locator(".edge[data-type='hierarchy']")).to_have_count(0)
            assert "hide_link=contains" in page.url
            # Share the link: the same view comes back, with nothing but the address.
            address = page.url
            await w.go("#/board")
            await page.goto(address.replace("#", "?x=1#", 1))
            await expect(svg(page)).to_have_attribute("data-ready", "true", timeout=SLOW)
            await expect(dot(page, f"node:{open_}")).to_have_count(1)
            await expect(dot(page, f"node:{done}")).to_have_count(0)
            await expect(svg(page).locator(".edge[data-type='hierarchy']")).to_have_count(0)
            # The same switches filter the list, whose rows are the same records.
            await show(page, presentation="list")
            await list_as(page, {f"node:{n}" for n in (epic, story, open_)})
            assert w.problems == [], w.problems


async def test_choosing_a_dot_opens_the_panel_and_the_local_graph_is_the_neighbours(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Around {word}")
        story = await made(client, "story", f"Chosen story {word}", epic, target_on="2031-05-06")
        sibling = await made(client, "story", f"Sibling story {word}", epic)
        task = await made(client, "task", f"Chosen task {word}", story)
        other = await made(client, "task", f"Other task {word}", story)
        await ok(
            client,
            "add_link",
            actor="a",
            source_id=story,
            target_id=sibling,
            link_type="relates_to",
        )
        rule = (await ok(client, "create_rule", actor="a", title=f"A rule {word}", story_id=story))[
            "id"
        ]
        decision = await make_decision(client, f"A decision {word}", node_ids=[story])
        fact = await make_fact(client, f"A fact {word}", node_ids=[story])
        # The index has caught up, so a panel says the same whenever it is opened.
        await reindexed(client)
        everything = f"view=everything&epic={epic}"
        wanted = {
            f"node:{story}", f"node:{epic}", f"node:{sibling}", f"node:{task}", f"node:{other}",
            f"rule:{rule}", f"decision:{decision['id']}", f"fact:{fact['id']}",
        }  # fmt: skip

        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, f"#/graph?{everything}")
            await dot(page, f"node:{story}").click()
            card = panel(page).locator(".card-detail")
            await expect(card).to_contain_text(f"#{story} Chosen story", timeout=SLOW)
            await expect(card).to_contain_text("Target 2031-05-06")
            await expect(card.locator("[data-section=facts] li.record")).to_contain_text(
                f"A fact {word}"
            )
            await expect(dot(page, f"node:{story}")).to_have_class(re.compile(r"\bchosen\b"))
            await same_as_the_boards_in(w, f"node:{story}")

            # The local graph at one step: exactly the record and what it is joined to.
            await graph_open(w, f"#/graph?scope=local&sel=node:{story}&{everything}")
            await drawn_as(page, wanted, await _edges_among(client, wanted))
            neighbours = page.locator("#graph-neighbours button.neighbour")
            near: list[str] = await neighbours.evaluate_all("els => els.map(e => e.dataset.key)")
            assert sorted(near) == sorted(wanted - {f"node:{story}"})
            # The same records as a list.
            await show(page, presentation="list")
            await list_as(page, wanted)
            await show(page, presentation="map")
            # Two steps reach one further: the other story of the epic is two steps from the task.
            await graph_open(w, f"#/graph?scope=local&sel=node:{task}&depth=2&{everything}")
            await expect(dot(page, f"node:{other}")).to_have_count(1, timeout=SLOW)
            await expect(dot(page, f"node:{sibling}")).to_have_count(1)
            await graph_open(w, f"#/graph?scope=local&sel=node:{task}&{everything}")
            await expect(dot(page, f"node:{story}")).to_have_count(1, timeout=SLOW)
            assert await dot(page, f"node:{sibling}").count() == 0

            # The neighbour list offers the same choices as a click on the dot.
            await graph_open(w, f"#/graph?scope=local&sel=node:{story}&{everything}")
            await page.locator(f"#graph-neighbours button[data-key='node:{task}']").click()
            await expect(dot(page, f"node:{task}")).to_have_class(re.compile(r"\bchosen\b"))
            await expect(page.locator("#card-title")).to_contain_text(f"#{task} Chosen task")
            await dot(page, f"node:{story}").click()
            await expect(dot(page, f"node:{story}")).to_have_class(re.compile(r"\bchosen\b"))
            await expect(page.locator("#card-title")).to_contain_text(f"#{story} Chosen story")
            assert w.problems == [], w.problems


async def _edges_among(client: Client, wanted: set[str]) -> set[tuple[str, str, str]]:
    return {line for line in await server_edges(client) if line[1] in wanted and line[2] in wanted}


async def test_the_whole_graph_can_be_reached_and_chosen_from_the_keyboard(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Keys {word}")
        story = await made(client, "story", f"Keyed story {word}", epic)
        task = await made(client, "task", f"Keyed task {word}", story)
        fact = await make_fact(client, f"Keyed fact {word}", node_ids=[story])
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=everything&epic={epic}")
            # A dot takes the focus, is chosen with Enter, and is pinned with P (and let go again).
            await dot(page, f"node:{story}").focus()
            await page.keyboard.press("p")
            await expect(dot(page, f"node:{story}")).to_have_class(re.compile(r"\bpinned\b"))
            await page.keyboard.press("p")
            await expect(dot(page, f"node:{story}")).not_to_have_class(re.compile(r"\bpinned\b"))
            await dot(page, f"node:{story}").focus()
            await page.keyboard.press("Enter")
            await expect(page.locator("#card-title")).to_contain_text(f"#{story} Keyed story")
            await dot(page, f"fact:{fact['id']}").focus()
            await page.keyboard.press("Enter")
            await expect(
                panel(page).locator(f".record-detail[data-record='fact:{fact['id']}']")
            ).to_be_visible(timeout=SLOW)
            await expect(dot(page, f"fact:{fact['id']}")).to_have_attribute("aria-pressed", "true")

            # The list is as reachable: the tree walks with the arrow keys, and Enter chooses.
            await show(page, presentation="list")
            await list_as(page, {f"node:{n}" for n in (epic, story, task)} | {f"fact:{fact['id']}"})
            first = page.locator("#work-tree li[role=treeitem][tabindex='0']")
            await first.focus()
            await expect(first).to_have_attribute("data-key", re.compile(r"^node:"))
            await page.keyboard.press("Home")
            await expect(page.locator(f"#work-tree li[data-key='node:{epic}']")).to_be_focused()
            await page.keyboard.press("ArrowDown")
            await expect(page.locator(f"#work-tree li[data-key='node:{story}']")).to_be_focused()
            await page.keyboard.press("ArrowDown")
            await expect(page.locator(f"#work-tree li[data-key='node:{task}']")).to_be_focused()
            await page.keyboard.press("Enter")
            await expect(page.locator("#card-title")).to_contain_text(f"#{task} Keyed task")
            # Opening a record moves the keyboard to its details; Shift+Tab-ing back is not needed
            # to carry on in the tree: the row takes the focus again.
            await expect(page.locator("#card-title")).to_be_focused()
            await page.locator(f"#work-tree li[data-key='node:{task}']").focus()
            await page.keyboard.press("ArrowLeft")
            await expect(page.locator(f"#work-tree li[data-key='node:{story}']")).to_be_focused()
            await page.keyboard.press("ArrowLeft")  # closes the story: its task is out of the tree
            await expect(page.locator(f"#work-tree li[data-key='node:{task}']")).to_be_hidden()
            await page.keyboard.press("ArrowRight")
            await expect(page.locator(f"#work-tree li[data-key='node:{task}']")).to_be_visible()
            # The records of context are reached by their titles, with the arrow keys too.
            await listed(page, f"fact:{fact['id']}").locator(".record-title").focus()
            await page.keyboard.press("Enter")
            await expect(
                panel(page).locator(f".record-detail[data-record='fact:{fact['id']}']")
            ).to_be_visible(timeout=SLOW)
            assert w.problems == [], w.problems


# -- what happens elsewhere shows without a reload ----------------------------------------


async def test_what_an_agent_or_the_board_changes_shows_on_the_map_and_the_list_without_a_reload(
    server_url: str,
) -> None:
    word = unique_word()
    async with fresh_project(server_url) as (client, board, project):
        epic = await made(client, "epic", f"Billing {word}")
        other_epic = await made(client, "epic", f"Elsewhere {word}")
        s1 = await made(client, "story", f"First story {word}", epic)
        s2 = await made(client, "story", f"Second story {word}", epic)
        t1 = await made(client, "task", f"Task one {word}", s1)
        t3 = await made(client, "task", f"Task three {word}", s2)
        await ok(client, "add_link", actor="a", source_id=s1, target_id=s2, link_type="relates_to")
        async with _browser(board, project) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=everything&epic={epic}")
            await expect(edge(page, "hierarchy", f"node:{s1}", f"node:{t1}")).to_have_count(1)

            # An agent adds a link, records a fact, and the map shows both.
            await ok(
                client, "add_link", actor="a", source_id=t3, target_id=t1, link_type="needs_first"
            )
            await expect(edge(page, "needs_first", f"node:{t3}", f"node:{t1}")).to_have_count(
                1, timeout=SLOW
            )
            fact = await make_fact(client, f"Heard {word}", node_ids=[t1])
            fact_key = f"fact:{fact['id']}"
            await expect(dot(page, fact_key)).to_have_attribute(
                "data-status", "active", timeout=SLOW
            )
            await expect(edge(page, "concerns", fact_key, f"node:{t1}")).to_have_count(1)
            # And the list, which is drawn from the same records, shows the fact when asked.
            await show(page, presentation="list")
            await expect(listed(page, fact_key)).to_be_visible(timeout=SLOW)
            await ok(client, "withdraw_fact", actor="a", fact_ids=[fact["id"]], reason="Misheard")
            await expect(listed(page, fact_key)).to_have_count(0, timeout=SLOW)
            await show(page, presentation="map")
            await expect(dot(page, fact_key)).to_have_count(0, timeout=SLOW)

            # A story moved on the board changes its hierarchy line in the graph.
            await w.go(f"#/board?epic={epic}&sel=node:{s2}")
            await page.locator("#move-parent").select_option(str(other_epic))
            await page.get_by_role("button", name="Move", exact=True).click()
            await expect(
                panel(page).locator(".card-detail > p", has_text="In epic")
            ).to_contain_text(f"#{other_epic} Elsewhere", timeout=SLOW)
            assert (await ok(client, "get_node", node_id=s2))["parent_id"] == other_epic
            await w.go(f"#/graph?view=everything&epic={other_epic}")
            await expect(edge(page, "hierarchy", f"node:{other_epic}", f"node:{s2}")).to_have_count(
                1, timeout=SLOW
            )
            await w.go(f"#/graph?view=everything&epic={epic}")
            await expect(edge(page, "hierarchy", f"node:{epic}", f"node:{s2}")).to_have_count(
                0, timeout=SLOW
            )
            # It is still shown, as something outside this epic: it is linked to a story here.
            await expect(dot(page, f"node:{s2}")).to_have_attribute("data-outside", "true")
            assert w.problems == [], w.problems


# -- everything, and above the limit ------------------------------------------------------


@pytest.fixture
def stack() -> Iterator[SecondStack]:
    second = SecondStack()
    try:
        yield second
    finally:
        second.close()


async def test_everything_shows_every_item_and_link_and_collapses_each_epic_above_the_limit(
    stack: SecondStack,
) -> None:
    stack.up()
    forty: list[dict[str, Any]] = []
    for epic in range(3):
        forty.append({"key": f"e{epic}", "kind": "epic", "title": f"Epic {epic}"})
        for story in range(3):
            name = f"s{epic}{story}"
            forty.append(
                {"key": name, "kind": "story", "title": f"Story {name}", "parent": f"e{epic}"}
            )
    for number in range(28):
        parent = f"s{number % 3}{(number // 3) % 3}"
        forty.append(
            {"key": f"t{number}", "kind": "task", "title": f"Task {number}", "parent": parent}
        )
    assert len(forty) == 40
    links = [
        {"source": "t1", "target": "t0", "link_type": "needs_first"},
        {"source": "t2", "target": "t1", "link_type": "needs_first"},
        {"source": "s01", "target": "s00", "link_type": "needs_first"},
        {"source": "s10", "target": "s00", "link_type": "relates_to"},
        {"source": "s21", "target": "s11", "link_type": "supersedes"},
        {"source": "e1", "target": "e2", "link_type": "duplicates"},
    ]
    board = board_url(stack.url, stack.project)
    async with stack.client() as client:
        await ok(client, "create_plan", actor="agent-1", items=forty, links=links)
        nodes = await all_nodes(client)
        known: dict[int, dict[str, Any]] = {}
        for node in nodes:
            for link in (await ok(client, "list_links", node_id=node["id"]))["items"]:
                known[link["id"]] = link
        assert len(nodes) == 40 and len(known) == len(links)

        async with browser_page(board, token=stack.key) as w:
            page = w.page
            await graph_open(w, "#/graph?scope=all")
            await expect(svg(page)).to_have_attribute("data-dots", "40")
            assert await dot_keys(page) == sorted(f"node:{n['id']}" for n in nodes)
            for link in known.values():
                await expect(
                    edge(
                        page,
                        link["link_type"],
                        f"node:{link['source_id']}",
                        f"node:{link['target_id']}",
                    )
                ).to_have_count(1)
            assert await svg(page).locator(".edge[data-type='hierarchy']").count() == 37
            assert await svg(page).locator(".edge").count() == 37 + len(known)

            # Everything adds the context: a requirement in a story, a decision, a fact about both.
            first_task = next(n for n in nodes if n["title"] == "Task 0")
            story = first_task["parent_id"]
            rule = (await ok(client, "create_rule", actor="a", title="A rule", story_id=story))[
                "id"
            ]
            decision = await make_decision(client, "A decision", node_ids=[first_task["id"]])
            fact = await make_fact(
                client, "A fact", node_ids=[first_task["id"]], decision_ids=[decision["id"]]
            )
            await show(page, view="everything")
            context = {f"rule:{rule}", f"decision:{decision['id']}", f"fact:{fact['id']}"}
            await expect(svg(page)).to_have_attribute("data-dots", "43", timeout=SLOW)
            assert await dot_keys(page) == sorted({f"node:{n['id']}" for n in nodes} | context)
            assert await svg(page).locator(".edge").count() == 37 + len(known) + 4
            await show(page, view="work")
            await expect(svg(page)).to_have_attribute("data-dots", "40", timeout=SLOW)

            # A graph with more than a thousand dots: each epic becomes one dot.
            big: list[dict[str, Any]] = []
            for epic in range(10):
                big.append({"key": f"E{epic}", "kind": "epic", "title": f"Big epic {epic}"})
                for story_number in range(10):
                    name = f"S{epic}-{story_number}"
                    big.append({"key": name, "kind": "story", "title": name, "parent": f"E{epic}"})
                    for task in range(10):
                        big.append(
                            {
                                "key": f"{name}-{task}",
                                "kind": "task",
                                "title": f"T{epic}{story_number}{task}",
                                "parent": name,
                            }
                        )
            planned = await ok(client, "create_plan", actor="agent-1", items=big)
            ids = {item["key"]: item["id"] for item in planned["created"]}
            collapsed = svg(page)
            await expect(collapsed).to_have_attribute("data-collapsed", "true", timeout=SLOW)
            await expect(collapsed).to_have_attribute("data-dots", "13")
            notice = await page.locator("#graph-notice").inner_text()
            assert "each epic is one dot" in notice and "1,150 dots" in notice, notice
            first = dot(page, f"node:{ids['E3']}")
            await expect(first).to_have_attribute("data-collapsed", "true")
            await first.click()
            # Opening one epic shows its items.
            await expect(collapsed).to_have_attribute("data-collapsed", "false")
            expected = sorted(
                f"node:{item}" for key, item in ids.items() if key == "E3" or key.startswith("S3-")
            )
            assert len(expected) == 111
            assert await dot_keys(page) == expected
            assert w.problems == [], w.problems
