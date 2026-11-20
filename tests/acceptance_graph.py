"""Chapter 9 of the acceptance scenario: the Graph and the Roadmap (M10, M11, M16 and M20), driven
in a real headless browser against the real containers and checked against a real MCP client. The
Graph is a presentation of the same work and context: Work, Context and Everything, each as a Map
or a List, with one chosen dot and one scope that survive every switch. A click on a dot chooses
it, says what it is under the map and Open page leads to the full page of the record (nothing opens
at the side), whose path keeps the Graph and brings it back as it was left. The Roadmap schedules
the same work with the dependency and date checks of the server, a bar leading to the same pages.
Table and Outline are gone: an item is changed on its own page, and moved with `move_node` there or
from the Graph's list. An epic, story or task is named by its kind and number (`story:12`, shown
as S-12) in every address, row and line, and every record has a page of its own (M19, M20).

Most of it works in projects made for the purpose (an account, a project and an agent key, made as
a person would), so that every list and count can be compared in full. The "Everything" proof needs
a graph of a known size, so it runs on a second, empty copy of Vrum that is made for it and removed
after it."""

import re
from typing import Any

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import expect

from tests.acceptance_board import back_to_view, same_words
from tests.acceptance_scenario import URL, Failed, chapter, clip, proof
from tests.board_context import (
    ADA,
    REMOVED_WORDS,
    check_same_relationships,
    choose_dot,
    chosen,
    chosen_box,
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
    open_chosen,
    refusal_in,
    server_edges,
    show,
    svg,
)
from tests.board_helpers import (
    SLOW,
    apply_filter,
    bar_of,
    board_url,
    browser_page,
    cards_are,
    crumbs,
    database_footprint,
    hold_back_news,
    item_loaded,
    item_page,
    made,
    on,
    open_item,
    plain,
    roadmap_ids,
    timeline,
)
from tests.board_helpers import drag_bar as drag
from tests.helpers import (
    all_nodes,
    code_of,
    day,
    make_decision,
    make_fact,
    number_of,
    ok,
    refused,
    reindexed,
    run_sql,
    unique_word,
)
from tests.second_stack import SecondStack
from tests.test_board_graph_integration import Expect, pressed
from tests.test_board_integration import twelve
from tests.test_board_requirements_integration import (
    LIST,
    choose_place,
    group,
    move_by_drag,
    rule_in,
    rule_row,
    summary_says,
)


async def graph() -> None:
    chapter("The Graph and the Roadmap: the same work and context, as a map, a list and a timeline")
    try:
        await presets_and_presentations()
        await selection_and_scope_stay()
        await unassigned_requirements()
        await connections_and_references()
        await no_removed_taxonomy()
        await choosing_a_dot()
        await filters_are_one_language()
        await everything()
        await the_roadmap()
        await dragging_bars()
        await keys_on_bars()
        await addresses_store_nothing()
    except BrowserError as error:
        raise Failed(
            f"M16: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- the presets, each as a map and as a list ---------------------------------------------------


async def presets_and_presentations() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        ids = await context_world(client, word)
        want = Expect(ids)
        relationships = await server_edges(client)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await graph_open(w, f"#/graph?epic={number_of(ids['E'])}")
            assert await pressed(page) == ("work", "map")
            checked = 0
            for view, (dots, map_lines, rows) in want.by_view.items():
                await show(page, view=view, presentation="map")
                await drawn_as(page, dots, map_lines)
                await check_same_relationships(page, rows)
                await show(page, presentation="list")
                await list_as(page, rows)
                for key in rows:
                    kind, ident = key.split(":")
                    if kind in {"epic", "story", "task"}:
                        # Work is named by its reference and its number in its own kind.
                        await expect(listed(page, key)).to_have_attribute("data-ref", key)
                        await expect(listed(page, key)).to_have_attribute("data-number", ident)
                    else:
                        await expect(listed(page, key)).to_have_attribute("data-id", ident)
                        tool = {"rule": "get_rule", "decision": "get_decision", "fact": "get_fact"}
                        record = await ok(client, tool[kind], **{f"{kind}_id": int(ident)})
                        row = listed(page, key)
                        await expect(row).to_have_attribute("data-status", record["status"])
                        if kind != "rule":
                            await expect(row).to_have_attribute(
                                "data-confirmation", record["confirmation"]
                            )
                    checked += 1
                await show(page, presentation="map")
            # What has ended is left out until asked for, and then every line the map draws is a
            # relationship the tools report, and the other way round.
            await show(page, view="everything")
            await page.locator("[name=ended]").check()
            ended = {
                f"{kind}:{ids[name]}"
                for kind, names in (
                    ("rule", ["rd"]),
                    ("decision", ["d_old", "d_gone"]),
                    ("fact", ["f_old", "f_gone"]),
                )
                for name in names
            }
            shown = want.work | want.records | ended
            await drawn_as(
                page, shown, {e for e in relationships if e[1] in shown and e[2] in shown}
            )
            for key in ended:
                await expect(dot(page, key)).to_have_class(re.compile(r"\bended\b"))
            assert w.problems == [], w.problems
    proof(
        "M16",
        "in the Graph, Work, Context and Everything each draw the records and lines the tools "
        "report, and the List of each holds the same records with the same relationships and the "
        "lifecycle and confirmation the tools give; what has ended appears only when asked for",
        True,
        f"{len(relationships)} relationships in the project, {checked} list rows compared",
    )


async def selection_and_scope_stay() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        ids = await context_world(client, word)
        want = Expect(ids)
        epic, story = ids["E"], ids["s1"]
        epic_n = str(number_of(epic))
        stories = {ids[name] for name in ("E", "s1", "s2")}
        address = f"#/graph?view=everything&epic={epic_n}&q=kind:story&sel={story}"
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await graph_open(w, address)
            await expect(chosen(page)).to_have_attribute("data-key", story, timeout=SLOW)
            steps = [("context", "map"), ("context", "list"), ("everything", "list")]
            steps += [("work", "list"), ("work", "map"), ("everything", "map")]
            for view, presentation in steps:
                await show(page, view=view, presentation=presentation)
                if presentation == "list" and view != "context":
                    await expect(listed(page, story)).to_have_attribute("aria-current", "true")
                if presentation == "map" and view != "context":
                    await expect(chosen(page)).to_have_attribute("data-key", story)
                params = await hash_params(page)
                assert (params["epic"], params["q"], params["sel"]) == (
                    epic_n,
                    "kind:story",
                    story,
                ), (view, presentation, params)
                assert (params["view"], params.get("as", "map")) == (view, presentation)
                assert await pressed(page) == (view, presentation)
                assert await page.locator("#graph-epic").input_value() == epic_n
            # The map marks the chosen record, and the list marks the one the map chose.
            await show(page, view="everything", presentation="map")
            fact = f"fact:{ids['f1']}"
            await choose_dot(page, fact)
            await show(page, presentation="list")
            await expect(listed(page, fact)).to_have_attribute("aria-current", "true")
            await show(page, presentation="map")
            await expect(dot(page, fact)).to_have_class(re.compile(r"\bchosen\b"))
            # A trip to the chosen record's page and back by the crumb leaves the Graph as it was.
            before = await hash_params(page)
            await open_chosen(page, fact)
            from_the_graph = (await crumbs(page))[2] == "Graph"
            await back_to_view(page, "Graph")
            await expect(dot(page, fact)).to_have_class(re.compile(r"\bchosen\b"), timeout=SLOW)
            came_back = await hash_params(page) == before
            # The address alone brings the whole state back: reloaded, and in a page of its own.
            await show(page, view="context", presentation="list")
            again = await page.evaluate("location.hash")
            await page.reload()
            await expect(page.locator("#graph-list")).to_be_visible(timeout=SLOW)
            assert await pressed(page) == ("context", "list")
            await expect(listed(page, fact)).to_have_attribute("aria-current", "true", timeout=SLOW)
            await list_as(page, want.by_view["context"][2])
            other = await page.context.new_page()
            await other.goto(f"{board}{again}")
            await expect(other.locator("#graph-list")).to_be_visible(timeout=SLOW)
            assert await other.evaluate("location.hash") == again
            await expect(other.locator("#graph-view-context")).to_have_attribute(
                "aria-pressed", "true"
            )
            await expect(listed(other, fact)).to_have_attribute(
                "aria-current", "true", timeout=SLOW
            )
            await other.close()
            # Around the chosen record, one step, in either presentation: the same records.
            await w.go(f"#/graph?view=everything&epic={epic_n}&sel={fact}")
            await page.locator("#graph-reach").select_option("1")
            around = {fact, story, f"rule:{ids['r1']}"}
            await drawn_as(
                page,
                around,
                {("concerns", fact, story), ("concerns", fact, f"rule:{ids['r1']}")}
                | {("placed", f"rule:{ids['r1']}", story)},
            )
            await show(page, presentation="list")
            await list_as(page, around)
            assert (await hash_params(page))["scope"] == "local"
            assert w.problems == [], w.problems
    proof(
        "M16",
        "the chosen dot and the scope (epic, filter, one step around) survive six switches of "
        "preset and presentation, the list marks the dot the map chose, a trip to the chosen "
        "record's page and back by the crumb brings the Graph back as it was, and the address "
        "alone brings the state back after a reload and in a second page",
        len(stories) == 3 and from_the_graph and came_back,
        f"{len(steps)} switches, address {again.split('?')[1][:60]}",
    )


# -- an unplaced acceptance criterion, and the coverage of the stories ---------------------------


async def unassigned_requirements() -> None:
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", "Acceptance criteria")
        first = await made(client, "story", "First story", epic)
        second = await made(client, "story", "Second story", epic)
        await made(client, "story", "Third story", epic)
        await rule_in(client, "Anchor", second)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await graph_open(w, LIST)
            start = await summary_says(page, client)
            await page.locator("#new-requirement").click()
            box = dialog(page)
            await box.locator("#requirement-title").fill("Created in the browser")
            await box.get_by_role("button", name="Add the acceptance criterion").click()
            # A new acceptance criterion opens on a page of its own, which says it is not placed
            # yet, and the Graph crumb brings the list back with it first among those not placed.
            await expect(item_page(page)).to_have_attribute("data-kind", "rule", timeout=SLOW)
            await expect(page.locator("#item-what")).to_contain_text(
                "not placed on a story or task"
            )
            on_its_page = int((await item_page(page).get_attribute("data-ref") or "").split(":")[1])
            await back_to_view(page, "Graph")
            newest = page.locator("#unplaced-group li.requirement").first
            await expect(newest).to_contain_text("Created in the browser", timeout=SLOW)
            rule = int(await newest.get_attribute("data-id") or 0)
            loose = await ok(client, "get_rule", rule_id=rule)
            after_new = await summary_says(page, client)
            await newest.get_by_role("button", name="Put on a story or task…").click()
            box = dialog(page)
            await choose_place(box, first)
            await box.get_by_role("button", name="Put it there").click()
            await expect(rule_row(page, rule)).to_have_attribute("data-node", first, timeout=SLOW)
            after_place = await summary_says(page, client)
            await (
                rule_row(page, rule)
                .get_by_role("button", name="Move to another story or task…")
                .click()
            )
            box = dialog(page)
            await choose_place(box, second)
            await box.get_by_role("button", name="Move it there").click()
            await expect(group(page, second).locator(f"li[data-id='{rule}']")).to_be_visible(
                timeout=SLOW
            )
            await page.locator("#stories-without-requirements summary").click()
            bare = page.locator(f"#stories-without-requirements li[data-story='{first}']")
            await expect(bare).to_be_visible()
            await move_by_drag(page, rule_row(page, rule), bare)
            await expect(group(page, first).locator(f"li[data-id='{rule}']")).to_be_visible(
                timeout=SLOW
            )
            after_drag = await summary_says(page, client)
            # A blank title is refused as an agent's is, and writes nothing.
            blank = await refused(
                client, "create_rule", actor="x", title="", body="", status="open"
            )
            total = (await ok(client, "list_rules", limit=1))["total"]
            await page.locator("#new-requirement").click()
            box = dialog(page)
            await box.locator("#requirement-body").fill("A body with no title")
            await box.get_by_role("button", name="Add the acceptance criterion").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            blank_words = await refusal_in(page).inner_text()
            kept = await box.locator("#requirement-body").input_value()
            await box.get_by_role("button", name="Cancel").click()
            # Putting it on the story or task it is already on is refused in the server's words.
            same = await refused(
                client, "place_rule", actor="x", rule_id=rule, expected_version=4, node=first
            )
            await (
                rule_row(page, rule)
                .get_by_role("button", name="Move to another story or task…")
                .click()
            )
            box = dialog(page)
            await choose_place(box, first)
            await box.get_by_role("button", name="Move it there").click()
            await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
            same_story_words = await refusal_in(page).inner_text()
            await box.get_by_role("button", name="Cancel").click()
            assert w.problems == [], w.problems
        moved = await ok(client, "get_rule", rule_id=rule)
        events = (await ok(client, "rule_history", rule_id=rule, limit=200))["items"]
        proof(
            "M16",
            "an acceptance criterion written in the Graph's context list is not placed, opens on a "
            "page of its own and is listed first when the Graph is back, placed on a story from a "
            "dialog, moved to another and dragged to a story that had none, each in one step and "
            "each time the sentence above the list says what coverage_report says; the history "
            "names the person",
            loose["node"] is None
            and on_its_page == rule
            and after_new == (start[0] + 1, start[1])
            and after_place == (start[0], start[1] - 1)
            and after_drag == after_place
            and (moved["node"], moved["version"]) == (first, 4)
            and [(e["action"], e["actor"]) for e in events]
            == [
                ("rule_created", ADA),
                ("rule_placed", ADA),
                ("rule_moved", ADA),
                ("rule_moved", ADA),
            ],
            f"unplaced {start[0]} -> {after_new[0]} -> {after_place[0]}, "
            f"stories without {start[1]} -> {after_place[1]}",
        )
        proof(
            "M16",
            "a blank acceptance criterion title and a move to the story it is already on are "
            "refused in the words of the tools that own them, what was typed stays, and nothing "
            "is written",
            same_words(blank_words, blank)
            and same_words(same_story_words, same)
            and kept == "A body with no title"
            and (await ok(client, "list_rules", limit=1))["total"] == total,
            f'"{clip(same_story_words, 70)}"',
        )


# -- a connection, a backlink or a reference leads to the ordinary panel ----------------------


async def connections_and_references() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Followed {word}")
        story = await made(client, "story", f"Referred story {word}", epic)
        await made(client, "task", f"A task {word}", story)
        decision = await make_decision(client, f"Referred decision {word}", nodes=[story])
        fact = await make_fact(
            client,
            f"Refers to [[{story}]] and [[decision:{decision['id']}]] {word}",
            nodes=[story],
            decision_ids=[decision["id"]],
        )
        mentions = await ok(client, "find_mentions", decision_id=decision["id"])
        sources = {m["source"]["ref"] for m in mentions["items"]}
        fact_key, decision_key = f"fact:{fact['id']}", f"decision:{decision['id']}"
        epic_n = number_of(epic)
        await reindexed(client)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await graph_open(w, f"#/graph?view=everything&as=list&epic={epic_n}")
            row = listed(page, decision_key)
            await row.locator("details.mentioned-in summary").click()
            items = row.locator("details.mentioned-in li.mention")
            await expect(items).to_have_count(len(mentions["items"]), timeout=SLOW)
            shown = await items.evaluate_all("els => els.map(e => e.dataset.source)")
            await row.locator(f"li.mention[data-source='{fact_key}'] a.mention-link").click()
            await item_loaded(page, fact_key)
            by_backlink = (await crumbs(page))[2] == "Graph" and "from=graph" in page.url
            await back_to_view(page, "Graph")
            params = await hash_params(page)
            kept = (params["view"], params["as"], params["epic"]) == (
                "everything",
                "list",
                str(epic_n),
            )
            # A reference in the record's own words leads on to the page of what it names.
            await listed(page, fact_key).locator(".record-title").click()
            await item_loaded(page, fact_key)
            await item_page(page).locator(f".record-text .reference[data-ref='{story}']").click()
            await item_loaded(page, story)
            via_reference = (await crumbs(page))[2] == "Graph" and "from=graph" in page.url
            await back_to_view(page, "Graph")
            await listed(page, fact_key).locator("details.gv-connections summary").click()
            link = listed(page, fact_key).locator(
                f"details.gv-connections li[data-other='{story}'] a.gv-link"
            )
            await link.click()
            await item_loaded(page, story)
            via_connection = (await crumbs(page))[2] == "Graph" and "from=graph" in page.url
            # On the map a neighbour is chosen first, and its Open page leads on.
            await w.go(f"#/graph?view=everything&epic={epic_n}&sel={fact_key}")
            await expect(dot(page, fact_key)).to_have_class(re.compile(r"\bchosen\b"), timeout=SLOW)
            near = page.locator("#graph-neighbours button[data-key]")
            joined = await near.evaluate_all(
                "els => els.map(e => [e.dataset.key, e.dataset.relation])"
            )
            await page.locator(f"#graph-neighbours button[data-key='{decision_key}']").click()
            await expect(chosen(page)).to_have_attribute("data-key", decision_key, timeout=SLOW)
            await open_chosen(page, decision_key)
            await (
                item_page(page)
                .locator(f"[data-part=concerns] tr.record-line[data-ref='{story}'] a.record-title")
                .click()
            )
            await item_loaded(page, story)
            from_a_decision = (await crumbs(page))[2] == "Graph"
            await back_to_view(page, "Graph")
            await expect(dot(page, decision_key)).to_have_class(
                re.compile(r"\bchosen\b"), timeout=SLOW
            )
            assert w.problems == [], w.problems
        proof(
            "M16",
            "a backlink, a reference in a record's own words and a connection in the list each "
            "open the page of the record they name, and a neighbour on the map is chosen and then "
            "opened with Open page, every one with the Graph in the path (from=graph) and the "
            "crumb bringing the Graph back with its preset, presentation and scope; the backlinks "
            "are what find_mentions says",
            sorted(shown) == sorted(sources)
            and by_backlink
            and kept
            and via_reference
            and via_connection
            and from_a_decision
            and sorted(joined) == sorted([[story, "concerns"], [decision_key, "concerns"]]),
            f"{len(sources)} mention of the decision",
        )


# -- nothing of the removed taxonomy in any preset or presentation ---------------------------------


async def no_removed_taxonomy() -> None:
    word = unique_word()
    types = {"epic", "story", "task", "rule", "decision", "fact"}
    async with fresh_project(URL) as (client, board, project):
        ids = await context_world(client, word)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await graph_open(w, f"#/graph?epic={number_of(ids['E'])}&ended=1")
            looked = 0
            for view in ("work", "context", "everything"):
                for presentation in ("map", "list"):
                    await show(page, view=view, presentation=presentation)
                    await page.wait_for_load_state("networkidle")
                    text = await page.locator("body").text_content() or ""
                    assert not REMOVED_WORDS.search(text), (view, presentation)
                    found: list[list[str]] = await page.locator(
                        "#graph-page [data-kind]"
                    ).evaluate_all("els => els.map(e => [e.dataset.kind, e.dataset.type || ''])")
                    assert {k for k, _ in found} <= types and {t for _, t in found if t} <= types
                    looked += 1
            switches = await page.locator("[name^=kind-]").evaluate_all(
                "els => els.map(e => e.name)"
            )
            for key in (
                ids["s1"],
                f"rule:{ids['r1']}",
                f"decision:{ids['d1']}",
                f"fact:{ids['f1']}",
                f"fact:{ids['f_old']}",
            ):
                await open_item(w, key, "graph")
                await page.wait_for_load_state("networkidle")
                assert not REMOVED_WORDS.search(await item_page(page).text_content() or ""), key
            await graph_open(w, f"#/graph?view=context&as=list&epic={number_of(ids['E'])}")
            for button in ("#record-fact", "#record-decision", "#new-requirement"):
                await page.locator(button).click()
                await expect(dialog(page)).to_be_visible()
                assert not REMOVED_WORDS.search(await dialog(page).text_content() or ""), button
                await page.keyboard.press("Escape")
                await expect(dialog(page)).to_have_count(0)
            assert w.problems == [], w.problems
        stores = run_sql(
            "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND (table_name ILIKE '%belief%' "
            "OR table_name ILIKE '%memor%' OR table_name ILIKE '%principle%' "
            "OR table_name ILIKE '%context%')",
            plain=True,
        ).stdout.strip()
    proof(
        "M16",
        "no separate context store and no removed taxonomy appears in any of the six "
        "presentations, the page of each kind of record or the dialogs: only epics, stories, "
        "tasks, acceptance criteria, decisions and facts, read from the same tables the tools read",
        sorted(switches) == sorted(f"kind-{kind}" for kind in types) and stores == "",
        f"{looked} presentations read, no belief, principle or memory table",
    )


# -- choosing a dot, the local graph, the keyboard -------------------------------------------------


async def choosing_a_dot() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        epic = await made(client, "epic", f"Around {word}")
        story = await made(client, "story", f"Chosen story {word}", epic, target_on="2031-05-06")
        sibling = await made(client, "story", f"Sibling story {word}", epic)
        task = await made(client, "task", f"Chosen task {word}", story)
        other = await made(client, "task", f"Other task {word}", story)
        await ok(
            client,
            "add_link",
            actor="a",
            source=story,
            target=sibling,
            link_type="relates_to",
        )
        rule = (await ok(client, "create_rule", actor="a", title=f"A rule {word}", node=story))[
            "id"
        ]
        decision = await make_decision(client, f"A decision {word}", nodes=[story])
        fact = await make_fact(client, f"A fact {word}", nodes=[story])
        await reindexed(client)
        everything = f"view=everything&epic={number_of(epic)}"
        wanted = {story, epic, sibling, task, other} | {
            f"rule:{rule}",
            f"decision:{decision['id']}",
            f"fact:{fact['id']}",
        }
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await graph_open(w, f"#/graph?{everything}")
            await choose_dot(page, story)
            box = await chosen_box(page)
            says_what = (
                box["title"] == f"Story {code_of(story)} Chosen story {word}"
                and "Due 2031-05-06" in box["chips"]
                and box["open"] == f"#/item?item={story}&from=graph"
            )
            await open_chosen(page, story)
            here = item_page(page)
            await expect(here.locator(".card-summary .target")).to_contain_text("2031-05-06")
            await expect(here.locator("[data-section=facts] tr.record-line")).to_contain_text(
                f"A fact {word}"
            )
            await graph_open(w, f"#/graph?scope=local&sel={story}&{everything}")
            edges = {
                line
                for line in await server_edges(client)
                if line[1] in wanted and line[2] in wanted
            }
            await drawn_as(page, wanted, edges)
            near: list[str] = await page.locator("#graph-neighbours button.neighbour").evaluate_all(
                "els => els.map(e => e.dataset.key)"
            )
            await show(page, presentation="list")
            await list_as(page, wanted)
            await show(page, presentation="map")
            await page.locator(f"#graph-neighbours button[data-key='{task}']").click()
            await expect(dot(page, task)).to_have_class(re.compile(r"\bchosen\b"))
            await expect(chosen(page)).to_contain_text(f"{code_of(task)} Chosen task")
            # The keyboard: a dot takes the focus, Enter chooses it and Enter again opens its page,
            # P pins it and lets it go.
            await graph_open(w, f"#/graph?{everything}")
            await dot(page, story).focus()
            await page.keyboard.press("p")
            await expect(dot(page, story)).to_have_class(re.compile(r"\bpinned\b"))
            await page.keyboard.press("p")
            await dot(page, f"fact:{fact['id']}").focus()
            await page.keyboard.press("Enter")
            await expect(chosen(page)).to_have_attribute(
                "data-key", f"fact:{fact['id']}", timeout=SLOW
            )
            await expect(item_page(page)).to_have_count(0)
            await page.keyboard.press("Enter")
            await item_loaded(page, f"fact:{fact['id']}")
            await back_to_view(page, "Graph")
            await show(page, presentation="list")
            first = page.locator("#work-tree li[role=treeitem][tabindex='0']")
            await first.focus()
            await page.keyboard.press("Home")
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await expect(page.locator("#item-title")).to_be_focused(timeout=SLOW)
            assert w.problems == [], w.problems
    proof(
        "M10",
        "choosing a dot says what it is under the map (its code, title and due date), Open page "
        "opens its page with the date and the fact; the local graph at one step is exactly the "
        "record's neighbours, as a map and as a list, the neighbour list offers the same choices, "
        "and the keyboard reaches, chooses, opens and pins",
        says_what and sorted(near) == sorted(wanted - {story}),
        f"{len(wanted)} records around one story",
    )


# -- one filter language ------------------------------------------------------------------------


async def filters_are_one_language() -> None:
    async with fresh_project(URL) as (client, board, project):
        ids = await twelve(client)
        epic = number_of(ids["epic"])
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await w.open(f"#/board?q=epic:{epic}")
            everything = await all_nodes(client, query=f"epic:{epic}")
            await cards_are(page, {n["ref"] for n in everything if n["kind"] != "epic"})
            compared = 0
            for term in (
                f"epic:{epic} kind:task status:ready,in_progress -claimed priority:high "
                f"OR epic:{epic} kind:story",
                f"epic:{epic} claimed:ann,bob",
                f"epic:{epic} is:overdue",
                f"epic:{epic} status:blocked",
                f"epic:{epic} no:target kind:task",
                f"epic:{epic} target:{day(-5)}..{day(10)} -kind:epic",
            ):
                wanted = await all_nodes(client, query=term)
                await apply_filter(page, term)
                await cards_are(page, {n["ref"] for n in wanted if n["kind"] != "epic"})
                compared += 1
            bad = f"epic:{epic} colour:red"
            said = await refused(client, "list_nodes", query=bad)
            await apply_filter(page, bad)
            problem = page.locator("#filter-problem")
            await expect(problem).to_contain_text("there is no field called 'colour'", timeout=SLOW)
            plain(await problem.inner_text(), said)
            text = f"epic:{epic} kind:task -claimed priority:high,urgent"
            wanted_here = {n["ref"] for n in await all_nodes(client, query=text)}
            await w.open(f"#/graph?view=work&as=list&q={text.replace(' ', '+')}")
            rows = page.locator("#work-tree li[role=treeitem]")
            await expect(rows.first).to_be_visible(timeout=SLOW)
            keys = await rows.evaluate_all("els => els.map(e => e.dataset.key)")
            assert w.problems == [], w.problems
    proof(
        "M11",
        "one filter language: the Board's filter box gives the cards list_nodes gives for the "
        "same text, for each kind of term, a text that cannot be read is refused in list_nodes' "
        "words, and the Graph's work list keeps the items the same filter asks for",
        wanted_here <= set(keys),
        f"{compared} filters compared",
    )


# -- everything, and above the limit (a second, empty copy of Vrum) ------------------------------


async def everything() -> None:
    stack = SecondStack()
    try:
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
        links = [
            {"source": "t1", "target": "t0", "link_type": "needs_first"},
            {"source": "t2", "target": "t1", "link_type": "needs_first"},
            {"source": "s01", "target": "s00", "link_type": "needs_first"},
            {"source": "s10", "target": "s00", "link_type": "relates_to"},
            {"source": "s21", "target": "s11", "link_type": "supersedes"},
            {"source": "e1", "target": "e2", "link_type": "duplicates"},
        ]
        async with stack.client() as client:
            await ok(client, "create_plan", actor="agent-1", items=forty, links=links)
            nodes = await all_nodes(client)
            known: dict[int, dict[str, Any]] = {}
            for node in nodes:
                for link in (await ok(client, "list_links", node=node["ref"]))["items"]:
                    known[link["id"]] = link
            async with browser_page(board_url(stack.url, stack.project), token=stack.key) as w:
                page = w.page
                await graph_open(w, "#/graph?scope=all")
                await expect(svg(page)).to_have_attribute("data-dots", "40")
                every_item = await dot_keys(page) == sorted(n["ref"] for n in nodes)
                for link in known.values():
                    await expect(
                        edge(page, link["link_type"], link["source"], link["target"])
                    ).to_have_count(1)
                total = await svg(page).locator(".edge").count()
                proof(
                    "M10",
                    "the Everything view of forty items shows every item and every link that "
                    "list_nodes and list_links give",
                    every_item and total == 37 + len(known),
                    f"40 dots, {total} lines",
                )
                big: list[dict[str, Any]] = []
                for epic in range(10):
                    big.append({"key": f"E{epic}", "kind": "epic", "title": f"Big epic {epic}"})
                    for story_number in range(10):
                        name = f"S{epic}-{story_number}"
                        big.append(
                            {"key": name, "kind": "story", "title": name, "parent": f"E{epic}"}
                        )
                        big.extend(
                            {
                                "key": f"{name}-{task}",
                                "kind": "task",
                                "title": f"T{epic}{story_number}{task}",
                                "parent": name,
                            }
                            for task in range(10)
                        )
                planned = await ok(client, "create_plan", actor="agent-1", items=big)
                ids = {item["key"]: item["ref"] for item in planned["created"]}
                await expect(svg(page)).to_have_attribute("data-collapsed", "true", timeout=SLOW)
                notice = await page.locator("#graph-notice").inner_text()
                collapsed_dots = await svg(page).get_attribute("data-dots")
                await dot(page, ids["E3"]).click()
                await expect(svg(page)).to_have_attribute("data-collapsed", "false")
                opened = await dot_keys(page) == sorted(
                    ref for k, ref in ids.items() if k == "E3" or k.startswith("S3-")
                )
                proof(
                    "M10",
                    "above a thousand dots Everything shows each epic as one dot and says so, "
                    "and opening an epic shows its items",
                    "each epic is one dot" in notice and collapsed_dots == "13" and opened,
                    "13 dots for 1,150 items, then 111",
                )
    finally:
        stack.close()


# -- the Roadmap ----------------------------------------------------------------------------------


async def updates(client: Client, node: str) -> list[dict[str, Any]]:
    history = await ok(client, "node_history", node=node)
    return [event for event in history["items"] if event["action"] == "update"]


async def dates(client: Client, node: str) -> tuple[str | None, str | None]:
    item = await ok(client, "get_node", node=node)
    return item["start_on"], item["target_on"]


async def the_roadmap() -> None:
    async with fresh_project(URL) as (client, board, project):
        word = unique_word()
        epic = await made(client, "epic", f"Roadmap {word}")
        story = await made(client, "story", f"Delivery {word}", epic)
        spans = {
            "a": (5, 12),
            "b": (8, 15),
            "c": (-20, -10),
            "d": (20, 30),
            "e": (25, 26),
            "f": (-3, 1),
        }
        ids = {
            name: await made(
                client, "task", f"Task {name} {word}", story, start_on=day(s), target_on=day(e)
            )
            for name, (s, e) in spans.items()
        }
        undated = [await made(client, "task", f"Undated {n} {word}", story) for n in (1, 2)]
        await ok(
            client, "add_link", actor="a", source=ids["b"], target=ids["a"],
            link_type="needs_first",
        )  # fmt: skip
        async with browser_page(board, token=project.key) as w:
            page = w.page
            await w.open(f"#/roadmap?q=epic:{number_of(epic)}")
            everyone = await roadmap_ids(page, 8)
            line = await timeline(page)
            where_right = True
            for name, (s, e) in spans.items():
                el = bar_of(page, ids[name])
                left = await el.evaluate("e => parseFloat(e.style.left)")
                width = await el.evaluate("e => parseFloat(e.style.width)")
                where_right = (
                    where_right
                    and abs(left - (on(day(s)) - line["origin"]).days * line["ppd"]) < 1.5
                    and abs(width - (e - s + 1) * line["ppd"]) < 1.5
                    and await el.get_attribute("data-start") == day(s)
                    and await el.get_attribute("data-end") == day(e)
                )
            worked_out = [
                (
                    await bar_of(page, n).get_attribute("data-worked-out"),
                    await bar_of(page, n).get_attribute("data-start"),
                    await bar_of(page, n).get_attribute("data-end"),
                )
                for n in (epic, story)
            ]
            left_off = [
                await page.locator(f"#rm-scroll [data-ref='{ref}']").count() == 0 for ref in undated
            ]
            arrow = page.locator(".rm-deps path.dep")
            arrows = [
                (
                    await arrow.nth(i).get_attribute("data-from"),
                    await arrow.nth(i).get_attribute("data-to"),
                )
                for i in range(await arrow.count())
            ]
            conflicts: list[str] = await page.locator(".rm-label:has(.chip.conflict)").evaluate_all(
                "els => els.map(e => e.dataset.ref)"
            )
            overdue: list[str] = await page.locator(".rm-label:has(.chip.overdue)").evaluate_all(
                "els => els.map(e => e.dataset.ref)"
            )
            words = await page.locator(".rm-label .chip").all_inner_texts()
            ruler_cells = await page.locator(".rm-day").count()
            track_days = round(
                await page.locator(".rm-track").evaluate("e => e.getBoundingClientRect().width")
                / line["ppd"]
            )
            tails = page.locator(".rm-rows .rm-late")
            tail_cells = [
                (
                    await tails.nth(i).get_attribute("data-ref"),
                    await tails.nth(i).evaluate("e => parseFloat(e.style.width)") / line["ppd"],
                )
                for i in range(await tails.count())
            ]
            same_filters = True
            epic_n = number_of(epic)
            for text, dated in (
                (f"epic:{epic_n} target:{day(-30)}..{day(16)}", 4),
                (f"epic:{epic_n} is:overdue", 1),
                (f"epic:{epic_n} target:none kind:task", 0),
            ):
                found = await all_nodes(client, query=text)
                wanted = {n["ref"] for n in found if n["span_start"] or n["span_end"]}
                await apply_filter(page, text)
                same_filters = same_filters and await roadmap_ids(page, dated) == wanted
    proof(
        "M11",
        "the Roadmap draws each of six bars at the pixels its dates give, the epic and story as "
        "worked-out spans, and leaves off the two items that have no dates",
        everyone == {epic, story, *ids.values()}
        and where_right
        and worked_out == [("true", day(-20), day(30))] * 2
        and all(left_off),
        f"{line['ppd']:g} pixels a day, today {line['today']}",
    )
    proof(
        "M11",
        "the needs_first link is an arrow from the prerequisite, the schedule conflict is on the "
        "item that starts too early and the overdue mark on the past item, both in words",
        arrows == [(ids["a"], ids["b"])]
        and conflicts == [ids["b"]]
        and overdue == [ids["c"]]
        and any("Schedule conflict" in text for text in words)
        and any("Overdue" in text for text in words),
        ", ".join(sorted(words)),
    )
    proof(
        "M32",
        "the Roadmap has a ruler with a cell for every day, a bar fills whole cells so that the "
        "days an item was meant to take can be counted, and work that is late has a tail with a "
        "cell for each day it is late",
        ruler_cells == track_days and where_right and tail_cells == [(ids["c"], -spans["c"][1])],
        f"{ruler_cells} day cells, {tail_cells[0][1]:g} cells late"
        if tail_cells
        else f"{ruler_cells} day cells, no tail",
    )
    proof(
        "M11",
        "a date filter in the Roadmap (target:RANGE, is:overdue, target:none) gives the same "
        "references as the same text through MCP",
        same_filters,
    )


async def dragging_bars() -> None:
    async with fresh_project(URL) as (client, board, project):
        word = unique_word()
        epic = await made(client, "epic", f"Dragged {word}")
        story = await made(client, "story", f"Part {word}", epic)
        moved = await made(
            client, "task", f"Moved {word}", story, start_on=day(3), target_on=day(12)
        )
        ended = await made(
            client, "task", f"Ended {word}", story, start_on=day(4), target_on=day(14)
        )
        stale = await made(
            client, "task", f"Stale {word}", story, start_on=day(5), target_on=day(15)
        )
        wrong = await made(
            client, "task", f"Wrong {word}", story, start_on=day(6), target_on=day(16)
        )
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await w.open(f"#/roadmap?q=epic:{number_of(epic)}")
            await roadmap_ids(page, 6)
            await drag(page, moved, 5)
            await expect(bar_of(page, moved)).to_have_attribute("data-start", day(8), timeout=SLOW)
            await drag(page, ended, -3, "end")
            await expect(bar_of(page, ended)).to_have_attribute("data-end", day(11), timeout=SLOW)
            await hold_back_news(page)
            await ok(
                client, "update_node", actor="agent-1", node=stale, expected_version=1,
                start_on=day(1), target_on=day(2),
            )  # fmt: skip
            said_stale = await refused(
                client, "update_node", actor="x", node=stale, expected_version=1,
                start_on=day(10), target_on=day(20),
            )  # fmt: skip
            await drag(page, stale, 5)
            refusal = page.locator(".notice.refusal .notice-text", has_text="is at version 2")
            await expect(refusal).to_be_visible(timeout=SLOW)
            stale_words = await refusal.inner_text()
            await expect(bar_of(page, stale)).to_have_attribute("data-start", day(1), timeout=SLOW)
            said_wrong = await refused(
                client,
                "update_node",
                actor="x",
                node=wrong,
                expected_version=1,
                start_on=day(26),
            )
            await drag(page, wrong, 20, "start")
            late = page.locator(".notice.refusal .notice-text", has_text="Work cannot start after")
            await expect(late).to_be_visible(timeout=SLOW)
            wrong_words = await late.inner_text()
            await expect(bar_of(page, wrong)).to_have_attribute("data-start", day(6), timeout=SLOW)
        (first,) = await updates(client, moved)
        (second,) = await updates(client, ended)
        proof(
            "M11",
            "dragging a Roadmap bar moves both dates and dragging an end moves one, each a "
            "versioned change that get_node and the history show with the person and the versions",
            await dates(client, moved) == (day(8), day(17))
            and (first["actor"], first["before"]["version"], first["after"]["version"])
            == (ADA, 1, 2)
            and first["after"] == {"start_on": day(8), "target_on": day(17), "version": 2}
            and await dates(client, ended) == (day(4), day(11))
            and (second["actor"], second["after"]) == (ADA, {"target_on": day(11), "version": 2}),
            f"{day(3)}..{day(12)} -> {day(8)}..{day(17)}",
        )
        proof(
            "M11",
            "dragging a bar an agent changed meanwhile is refused with the stale-version message "
            "and the bar goes back; a start dragged past the target is refused with the MCP "
            "message",
            same_words(stale_words, said_stale)
            and same_words(wrong_words, said_wrong)
            and await dates(client, stale) == (day(1), day(2))
            and await dates(client, wrong) == (day(6), day(16))
            and [e["actor"] for e in await updates(client, stale)] == ["agent-1"]
            and await updates(client, wrong) == [],
            clip(wrong_words, 90),
        )


async def keys_on_bars() -> None:
    async with fresh_project(URL) as (client, board, project):
        word = unique_word()
        epic = await made(client, "epic", f"Keys {word}")
        story = await made(client, "story", f"Part {word}", epic)
        keyed = await made(
            client, "task", f"Keyed {word}", story, start_on=day(3), target_on=day(12)
        )
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await w.open(f"#/roadmap?q=epic:{number_of(epic)}")
            await roadmap_ids(page, 3)
            await bar_of(page, keyed).focus()
            for _ in range(3):
                await page.keyboard.press("ArrowRight")
            await expect(bar_of(page, keyed)).to_have_attribute("data-start", day(6), timeout=SLOW)
            await bar_of(page, keyed).focus()
            await page.keyboard.press("Shift+ArrowLeft")
            await expect(bar_of(page, keyed)).to_have_attribute("data-start", day(-1), timeout=SLOW)
        history = await updates(client, keyed)
        proof(
            "M11",
            "keys shift a bar by a day or a week (three presses are one change), each shown by "
            "get_node and the history",
            await dates(client, keyed) == (day(-1), day(8))
            and [(e["actor"], e["after"]["version"]) for e in history] == [(ADA, 2), (ADA, 3)],
            f"{day(-1)}..{day(8)}",
        )


async def addresses_store_nothing() -> None:
    async with fresh_project(URL) as (client, board, project):
        word = unique_word()
        epic = await made(client, "epic", f"Kept {word}")
        story = await made(client, "story", f"Kept story {word}", epic)
        task = await made(
            client, "task", f"Kept task {word}", story, start_on=day(1), target_on=day(5)
        )
        await make_fact(client, f"About the kept task {word}", nodes=[task])
        await reindexed(client)
        epic_n = number_of(epic)
        async with browser_page(board, token=project.key) as first:
            await first.open(f"#/roadmap?q=epic:{epic_n}")
            await roadmap_ids(first.page, 3)
            await first.page.locator("[data-zoom=quarter]").click()
            await expect(first.page.locator(".rm-track")).to_have_attribute("data-zoom", "quarter")
            await first.page.locator("#group").select_option("status")
            await expect(first.page.locator(".rm-group").first).to_be_visible(timeout=SLOW)
            roadmap_address = first.page.url
            # From the Roadmap a record has a page, and the search box leads to a page too: each
            # of them is its address.
            await first.page.locator(f".rm-label[data-ref='{task}'] a.rm-open").click()
            await item_loaded(first.page, task)
            item_address = first.page.url
            await first.page.locator("#global-search").fill(f"kept task {word}")
            await first.page.locator("#global-search-go").click()
            await expect(first.page.locator("#search-results tr.result").first).to_be_visible(
                timeout=SLOW
            )
            search_address = first.page.url
            results = await first.page.locator("#search-results tr.result").evaluate_all(
                "els => els.map(e => [e.dataset.type, e.dataset.ref])"
            )
            before = database_footprint()
            async with browser_page(board, token=project.key, name="Someone else") as second:
                await second.page.goto(roadmap_address)
                await roadmap_ids(second.page, 3)
                await expect(second.page.locator(".rm-track")).to_have_attribute(
                    "data-zoom", "quarter", timeout=SLOW
                )
                the_view = (
                    await second.page.locator("#filter").input_value() == f"epic:{epic_n}"
                    and await second.page.locator("#group").input_value() == "status"
                )
                await second.page.goto(item_address)
                await item_loaded(second.page, task)
                trail = await crumbs(second.page)
                its_page = trail[2] == "Roadmap" and trail[-1] == (
                    f"{code_of(task)} Kept task {word}"
                )
                await second.page.goto(search_address)
                await expect(second.page.locator("#search-results tr.result").first).to_be_visible(
                    timeout=SLOW
                )
                same = sorted(
                    await second.page.locator("#search-results tr.result").evaluate_all(
                        "els => els.map(e => [e.dataset.type, e.dataset.ref])"
                    )
                ) == sorted(results)
                assert second.problems == [], second.problems
            after = database_footprint()
        proof(
            "M11",
            "a Roadmap's address (filter, zoom, grouping), the address of the page of a record "
            "reached from it and the address of a search, each opened in a second browser, show "
            "the same, and the server stored nothing for any of them",
            same
            and the_view
            and its_page
            and after == before
            and all(
                part in roadmap_address
                for part in (f"q=epic:{epic_n}", "zoom=quarter", "group=status")
            )
            and item_address.endswith(f"#/item?item={task}&from=roadmap")
            and "#/search?" in search_address,
            roadmap_address.split("#", 1)[1][:90],
        )
