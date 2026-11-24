"""Chapter 9 of the acceptance scenario: the Graph and the Roadmap (M10, M11, M30, M32, M34 and
M38), driven in a real headless browser against the real containers and checked against a real MCP
client. The Graph has three tabs under one box that finds what to look at. Structure is the whole
project as a map that is alive: every epic, story and task is one dot, joined by the hierarchy
alone, with the links drawn when asked for, and it rests when nothing touches it. A dot opens the
page of its item (nothing opens beside the map). Around is the very `recall` an agent gets for the
item, drawn to fit the tab. Order is the plan of one epic, story or task, in phases or chains, and
asks what to look at when nothing is chosen. Notes and acceptance criteria are never dots of the
map. The Roadmap schedules the same work with the dependency and date checks of the server, and
fills the window with its grid. The filter language is one language: the Board's box, the Lists'
Work list and the Roadmap give what `list_nodes` gives. An epic, story or task is named by its
kind and number (`story:12`, shown as S-12) in every address and row.

Most of it works in projects made for the purpose (an account, a project and an agent key, made as
a person would), so that every list and count can be compared in full. A project of its own is also
what gives a graph of a known size: the whole-project map of forty items and the project that is
too big to start with its tasks are made in one."""

import re
from typing import Any
from urllib.parse import unquote

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import expect

from tests.acceptance_scenario import URL, Failed, chapter, clip, proof
from tests.board_context import (
    ADA,
    REMOVED_WORDS,
    context_world,
    dialog,
    fresh_project,
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
    go,
    hold_back_news,
    item_loaded,
    item_page,
    made,
    on,
    open_item,
    plain,
    roadmap_ids,
    signed_in_name,
    timeline,
)
from tests.board_helpers import drag_bar as drag
from tests.board_roadmap import box_of
from tests.helpers import (
    all_nodes,
    code_of,
    connect,
    day,
    make_decision,
    make_note,
    number_of,
    ok,
    refused,
    reindexed,
    run_sql,
    the_token,
    unique_word,
)
from tests.test_board_context_integration import context_list, notes_open
from tests.test_board_graph_integration import (
    AROUND_FITS,
    CARDS_OVERLAPPING,
    MAP,
    around_measured,
    around_open,
    busy_world,
    drawn,
    lit,
    note_ref,
    order_open,
    partners_of,
    phase_world,
    phases_from_the_tools,
    press_to_open,
    refs_in,
    says_via,
    structure_open,
)
from tests.test_board_graph_map_integration import (
    CROSSINGS,
    WATCH,
    at_rest,
    clear_log,
    demo_world,
    drift,
    far,
    frames,
    least_gap,
    mean_line,
    open_map,
    places,
    press_and_move,
    rest_log,
    span,
    toward_the_middle,
)
from tests.test_board_integration import twelve
from tests.test_board_roadmap_integration import crowded_world


def checked(detail: str, **checks: bool) -> tuple[bool, str]:
    """Whether every check holds, and the proof's detail, which names the ones that do not."""
    failed = [name for name, held in checks.items() if not held]
    return not failed, detail + (f"; does not hold: {', '.join(failed)}" if failed else "")


async def graph() -> None:
    chapter("The Graph and the Roadmap: the work as a map, around one item, in order and in time")
    try:
        await the_whole_project_as_a_map()
        await a_big_project_starts_at_its_stories()
        await a_dot_opens_its_page_and_the_box_finds_what_to_look_at()
        await the_map_is_alive()
        await around_is_what_recall_says()
        await around_fits_the_tab()
        await order_is_the_plan()
        await no_removed_taxonomy()
        await filters_are_one_language()
        await the_roadmap()
        await the_roadmap_fills_the_window()
        await dragging_bars()
        await keys_on_bars()
        await addresses_store_nothing()
    except BrowserError as error:
        raise Failed(
            f"M38: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- Structure: the whole project, its links when asked for, and what is lit -----------------------


def forty_items() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Three epics of three stories each and 28 tasks under them, with six links of four kinds."""
    items: list[dict[str, Any]] = []
    for epic in range(3):
        items.append({"key": f"e{epic}", "kind": "epic", "title": f"Epic {epic}"})
        for story in range(3):
            name = f"s{epic}{story}"
            items.append(
                {"key": name, "kind": "story", "title": f"Story {name}", "parent": f"e{epic}"}
            )
    for number in range(28):
        parent = f"s{number % 3}{(number // 3) % 3}"
        items.append(
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
    return items, links


async def the_whole_project_as_a_map() -> None:
    items, links = forty_items()
    async with fresh_project(URL) as (client, board, project):
        await ok(client, "create_plan", items=items, links=links)
        nodes = await all_nodes(client)
        wanted = sorted(n["ref"] for n in nodes)
        partners = {ref: await partners_of(client, ref) for ref in wanted}
        known = {
            (link["link_type"], link["source"], link["target"])
            for ref in wanted
            for link in (await ok(client, "list_links", node=ref, limit=200))["items"]
        }
        linked = [ref for ref, others in partners.items() if others]
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            # Structure is where the Graph opens, and it draws the whole project at once: every
            # epic, story and task is one dot or hub, joined by one line to what it is part of.
            await structure_open(w)
            every_item = await drawn(page) == wanted
            kinds = {
                kind: await page.locator(f"{MAP} g.graph-st-node.is-{kind}").count()
                for kind in ("epic", "story", "task")
            }
            hierarchy = await page.locator(f"{MAP} .graph-st-line").count()
            no_links_yet = await page.locator(f"{MAP} .graph-st-link").count() == 0
            on_structure = await page.locator("#graph-view").get_attribute("data-tab") == (
                "structure"
            )
            # Links between items are drawn when asked for: every link there is, once, of its
            # own kind.
            await page.locator("button[data-choice=links]").click()
            await expect(page.locator(f"{MAP} .graph-st-link")).to_have_count(len(known))
            by_kind = {}
            for kind in ("needs_first", "relates_to", "supersedes", "duplicates"):
                wanted_kind = len([1 for one in known if one[0] == kind])
                await expect(page.locator(f"{MAP} .graph-st-link.t-{kind}")).to_have_count(
                    wanted_kind
                )
                by_kind[kind] = wanted_kind
            still_all = await drawn(page) == wanted
            # Choosing an item (the address says which) lights what the tools say it is linked to
            # and draws exactly those links as its own.
            lights = []
            for ref in linked:
                await structure_open(w, f"#/graph?focus={ref}")
                lights.append(
                    await lit(page, "is-sel") == {ref}
                    and await lit(page, "is-linked") == partners[ref]
                )
                await expect(page.locator(f"{MAP} .graph-st-link.is-chosen")).to_have_count(
                    len([1 for one in known if ref in one[1:]])
                )
            alone = next(ref for ref in wanted if not partners[ref])
            await structure_open(w, f"#/graph?focus={alone}")
            nobody_lit = await lit(page, "is-linked") == set()
            # Context is never a dot of the map: a criterion on a story, a note someone decided and
            # a note about both it and the task are in the project now, and the map still draws
            # the same forty items. A note looked at lights the work it concerns.
            first_task = next(n for n in nodes if n["title"] == "Task 0")
            rule = (await ok(client, "create_rule", title="A rule", node=first_task["parent"]))[
                "id"
            ]
            decision = await make_decision(client, "A decision", nodes=[first_task["ref"]])
            about = await make_note(
                client,
                "A note about both",
                "Said at the review",
                nodes=[first_task["ref"]],
                note_ids=[decision["id"]],
            )
            await structure_open(w)
            only_work = await drawn(page) == wanted
            await structure_open(w, f"#/graph?focus=note:{about['id']}")
            note_lights = await drawn(page) == wanted and await lit(page, "is-linked") == {
                first_task["ref"]
            }
            await structure_open(w, f"#/graph?focus=rule:{rule}")
            rule_lights = await drawn(page) == wanted
            assert w.problems == [], w.problems
    proof(
        "M30",
        "Structure, where the Graph opens, draws the 40 epics, stories and tasks of a project "
        "once each, joined by one line to what each is part of and by no link until 'Show links "
        "between items' is chosen, which draws exactly the links list_links gives, each of its "
        "own kind",
        every_item
        and still_all
        and on_structure
        and kinds == {"epic": 3, "story": 9, "task": 28}
        and hierarchy == 40
        and no_links_yet
        and sum(by_kind.values()) == len(known) == len(links),
        f"{len(wanted)} dots, {hierarchy} lines, {len(known)} links",
    )
    proof(
        "M30",
        "choosing an item lights exactly the items the tools say it is linked to and draws those "
        "links as its own; a note, a criterion and a decision are never dots: with them in the "
        "project the map still draws the same forty items, and a note looked at lights the task "
        "it concerns",
        all(lights)
        and len(lights) == len(linked) == 10
        and nobody_lit
        and only_work
        and note_lights
        and rule_lights,
        f"{len(lights)} linked items compared",
    )


async def a_big_project_starts_at_its_stories() -> None:
    async with fresh_project(URL) as (client, board, project):
        big: list[dict[str, Any]] = []
        for epic in range(14):
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
        await ok(client, "create_plan", items=big)
        everything = (await ok(client, "list_nodes", limit=1))["total"]
        epics = (await ok(client, "list_nodes", kind="epic", limit=1))["total"]
        stories = (await ok(client, "list_nodes", kind="story", limit=1))["total"]
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await structure_open(w)
            await expect(page.locator("button[data-choice='level:stories']")).to_have_attribute(
                "aria-pressed", "true"
            )
            at_stories = len(await drawn(page)) == epics + stories
            no_tasks = await page.locator(f"{MAP} g.graph-st-node.is-task").count() == 0
            await page.locator("button[data-choice='level:tasks']").click()
            await expect(page.locator(f"{MAP} g.graph-st-node")).to_have_count(
                everything, timeout=SLOW
            )
            in_the_address = "level=tasks" in page.url
            # A map of this many dots is painted before it has settled, comes to rest all the same
            # and stays there.
            await expect(page.locator(MAP)).to_have_attribute(
                "data-resting", "true", timeout=6 * SLOW
            )
            assert w.problems == [], w.problems
    proof(
        "M30",
        "a project of more than fifteen hundred items starts Structure at its epics and stories "
        "and draws the tasks when they are asked for; the map of all of them comes to rest",
        everything == len(big) > 1500 and at_stories and no_tasks and in_the_address,
        f"{epics + stories} dots at first, then {everything}",
    )


# -- a dot opens its page, and the box at the top finds what to look at ---------------------------


async def a_dot_opens_its_page_and_the_box_finds_what_to_look_at() -> None:
    # A short word: the name written beside a story's circle is cut when it is long.
    word = "lookat"
    async with fresh_project(URL) as (client, board, project):
        world = await context_world(client, word, project)
        epic, other = world["E"], world["E2"]
        story, task = world["s1"], world["t1"]
        inside = {world[name] for name in ("s1", "s2", "t1", "t2", "t3")}
        everything = sorted(n["ref"] for n in await all_nodes(client))
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await structure_open(w)
            nothing_beside = await page.locator(".graph-x-side").count() == 0
            # The top bar names the page, so the page's own title is for a screen reader only.
            title = page.locator("#page h1")
            await expect(title).to_have_text("Graph")
            await expect(title).to_have_class(re.compile(r"\bsr-only\b"))
            whole = await page.evaluate("location.hash")
            opened = []
            for ref in (story, task, epic):
                await page.locator(f"{MAP} g.graph-st-node[data-ref='{ref}']").click()
                await expect(page.locator(f"#item-page[data-ref='{ref}']")).to_be_visible(
                    timeout=SLOW
                )
                opened.append(unquote(page.url).endswith(f"#/item?item={ref}&from=graph"))
                await page.go_back()
                await expect(page.locator(MAP)).to_be_visible(timeout=SLOW)
                await at_rest(page)
                opened.append(await page.evaluate("location.hash") == whole)
            for key in ("Enter", "Space"):
                await press_to_open(
                    page,
                    page.locator(f"{MAP} g.graph-st-node[data-ref='{task}']"),
                    key,
                    page.locator(f"#item-page[data-ref='{task}']"),
                )
                await page.go_back()
                await expect(page.locator(MAP)).to_be_visible(timeout=SLOW)
                await at_rest(page)
            # The item looked at is marked, with the items it is linked to lit, and opens nothing.
            await structure_open(w, f"#/graph?focus={task}")
            marked = await lit(page, "is-sel") == {task} and await lit(page, "is-linked") == (
                await partners_of(client, task)
            )
            nothing_beside = nothing_beside and await page.locator(".graph-x-side").count() == 0
            assert w.problems == [], w.problems
        proof(
            "M34",
            "a click, Enter or Space on a dot opens that item's page, with the Graph in its path, "
            "and the browser's Back brings the map back as it was left; nothing is drawn beside "
            "the map, the item looked at stays marked with its links lit, and the page's own "
            "title is for a screen reader only, since the top bar names the page",
            all(opened) and len(opened) == 6 and nothing_beside and marked,
            "an epic, a story and a task, by pointer and by keyboard",
        )

        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await structure_open(w)
            box = page.locator("#graph-find-input")
            await box.click()
            finds = []
            for typed, found in (
                (f"t3 {word}", [world["t3"]]),
                (code_of(world["s2"]), [world["s2"]]),
                (f"AC-{world['r1']}", [f"rule:{world['r1']}"]),
                (f"N-{world['d1']}", [f"note:{world['d1']}"]),
            ):
                await box.fill(typed)
                await expect(page.locator("#graph-find-popup .graph-find-option")).to_have_count(
                    len(found)
                )
                finds.append(await refs_in(page, "#graph-find-popup .graph-find-option") == found)
            # A criterion and a note are named by their short codes, AC-3 and N-7.
            chosen = page.locator("#graph-find-chosen")
            await box.fill(f"AC-{world['r1']}")
            await page.locator(
                f"#graph-find-popup .graph-find-option[data-ref='rule:{world['r1']}']"
            ).click()
            await expect(chosen).to_have_attribute("data-ref", f"rule:{world['r1']}", timeout=SLOW)
            short_codes = f"AC-{world['r1']}" in await chosen.inner_text()
            await page.locator("#graph-focus-clear").click()
            await expect(chosen).to_be_hidden()
            # One epic on its own: the scope select draws it as a tidy view with every name, and
            # the centre and the leaves open their pages.
            select = page.locator("#graph-st-scope")
            options: list[list[str]] = await select.locator("option").evaluate_all(
                "els => els.map(e => [e.value, e.textContent])"
            )
            await select.select_option(epic)
            await expect(page.locator("#graph-view svg.graph-rd-svg")).to_be_visible(timeout=SLOW)
            leaves: list[str] = await page.locator("svg.graph-rd-svg [data-ref]").evaluate_all(
                "els => els.map(e => e.dataset.ref)"
            )
            story_names: list[str] = await page.locator(
                "svg.graph-rd-svg .graph-rd-story-name"
            ).evaluate_all("els => els.map(e => e.textContent.replace(/\\s+/g, ''))")
            radial = await page.evaluate("location.hash")
            await page.locator(f"svg.graph-rd-svg [data-ref='{world['t3']}']").first.click()
            await expect(page.locator(f"#item-page[data-ref='{world['t3']}']")).to_be_visible(
                timeout=SLOW
            )
            leaf_opens = unquote(page.url).endswith(f"#/item?item={world['t3']}&from=graph")
            await page.go_back()
            await expect(page.locator("#graph-view svg.graph-rd-svg")).to_be_visible(timeout=SLOW)
            back_to_it = await page.evaluate("location.hash") == radial
            await select.select_option("")
            await expect(page.locator(MAP)).to_be_visible(timeout=SLOW)
            whole_again = await drawn(page) == everything and "scope=" not in page.url
            assert w.problems == [], w.problems
        held, detail = checked(
            f"{len(leaves)} items drawn for one epic",
            finds=all(finds),
            short_codes=short_codes,
            whole_project_first=options[0] == ["", "Whole project"],
            every_epic_offered={value for value, _ in options[1:]} == {epic, other},
            the_epic_alone=set(leaves) == {epic, *inside},
            story_names=sorted(story_names) == [f"s1{word}", f"s2{word}"],
            leaf_opens=leaf_opens,
            back_to_it=back_to_it,
            whole_again=whole_again,
        )
        proof(
            "M30",
            "the box at the top of the Graph finds an epic, story, task, acceptance criterion or "
            "note by a word of its title or by its code (S-2, AC-3, N-7) and shows the code of "
            "what is chosen; choosing an epic in Scope draws it alone, as a tidy view with the "
            "name of each story written, whose leaves open their pages and whose Back brings the "
            "view back, and 'Whole project' goes back to the map",
            held,
            detail,
        )


# -- the map is alive: it rests, a dragged dot pulls what hangs on it, and Tidy --------------------


async def the_map_is_alive() -> None:
    async with fresh_project(URL) as (client, board, project):
        demo = await demo_world(client, unique_word())
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await page.add_init_script(WATCH)
            await open_map(w)
            settled = await places(page)
            # Resting means resting: no frame is asked for, and nothing overlaps.
            asked = await frames(page)
            await page.wait_for_timeout(700)
            quiet = await frames(page) == asked and least_gap(settled) > 0.5
            task = demo.tasks[3]
            story = demo.parent[task]
            siblings = [one for one in demo.kids(story) if one != task]
            epic = demo.epic_of(task)
            others = sorted(
                (one for one in demo.epics if one != epic),
                key=lambda one: far(settled[one], settled[task]),
            )
            dx, dy = toward_the_middle(settled, task, 150)
            goal = [settled[task][0] + dx, settled[task][1] + dy]
            # Held 150 units away, the dot is where the pointer is, its story and its other tasks
            # have come toward it, and the farthest epic has hardly moved.
            await clear_log(page)
            await press_and_move(page, task, settled, dx, dy)
            await expect(page.locator(MAP)).to_have_attribute("data-resting", "false")
            await page.wait_for_timeout(700)
            dragged = await places(page)
            moved = drift(settled, dragged)
            at_the_pointer = far(dragged[task], goal) <= 3
            pulled = far(dragged[story], goal) < far(settled[story], goal) - 8
            closer = [far(dragged[one], goal) < far(settled[one], goal) for one in siblings]
            far_epic_still = moved[others[-1]] < 10 and moved[others[-1]] < moved[story] / 2
            # Let go: it has opened nothing, the map moves and rests, and every dot is back.
            await page.mouse.up()
            await at_rest(page)
            opened_nothing = await page.locator("#item-page").count() == 0
            log = await rest_log(page)
            after = await places(page)
            back = drift(settled, after)
            # One of the Forces: more Repel spreads the map out, and Reset gives the settled map.
            await page.locator("#graph-st-forces").click()
            await clear_log(page)
            await page.locator("#graph-st-force-repel").fill("3")
            await at_rest(page)
            force_log = await rest_log(page)
            spread = span(await places(page)) / span(settled)
            await page.locator("#graph-st-forces-reset").click()
            await at_rest(page)
            reset_back = max(drift(settled, await places(page)).values()) <= 1
            # Tidy glides to the tidy layout, where no line crosses another and no dots touch.
            await clear_log(page)
            await page.locator("#graph-st-tidy").click()
            await expect(page.locator(MAP)).to_have_attribute("data-resting", "false")
            await at_rest(page)
            tidy_log = await rest_log(page)
            tidy = await places(page)
            crossings = await page.evaluate(CROSSINGS)
            lines = await mean_line(page)
            assert w.problems == [], w.problems
    held, detail = checked(
        f"{len(settled) - 1} dots, spread {spread:.2f} times with Repel at 3",
        quiet=quiet,
        at_the_pointer=at_the_pointer,
        story_pulled=pulled,
        tasks_pulled=sum(closer) >= len(siblings) / 2,
        far_epic_still=far_epic_still,
        opened_nothing=opened_nothing,
        moved_then_rested=log[-1] == "true" and "false" in log,
        dot_back=back[task] <= 3,
        all_back=max(back.values()) <= 5,
        repel_moved_then_rested=force_log[-1] == "true" and "false" in force_log,
        repel_spreads=spread > 1.4,
        reset_back=reset_back,
        tidy_moved_then_rested=tidy_log[-1] == "true" and "false" in tidy_log,
        no_crossings=crossings == 0,
        no_touching=least_gap(tidy) > 0.5 and lines > 0,
    )
    proof(
        "M38",
        "the Structure map is alive: at rest it asks for no frame and no dots touch; a dot dragged "
        "away is where the pointer is, pulls its story and its tasks with it and leaves the far "
        "epics alone; let go, it opens nothing and the map moves and rests with every dot back "
        "where it was; more Repel spreads the map out and Reset gives the settled map back; Tidy "
        "glides to a layout where no line crosses another",
        held,
        detail,
    )


# -- Around: the very recall an agent gets, drawn to fit the tab -----------------------------------


async def around_is_what_recall_says() -> None:
    word = "around"
    async with fresh_project(URL) as (client, board, project):
        world = await context_world(client, word, project)
        s1, t1 = world["s1"], world["t1"]
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            looked = 0
            for name, target in (
                ("s1", {"node": s1}),
                ("s2", {"node": world["s2"]}),
                ("t1", {"node": t1}),
                ("E", {"node": world["E"]}),
                ("r1", {"rule_id": world["r1"]}),
            ):
                focus = f"rule:{world['r1']}" if "rule_id" in target else world[name]
                await around_open(w, focus)
                recalled = await ok(client, "recall", limit=50, **target)
                where = (name, focus)
                # The notes in force, in recall's order (the ones someone decided first); the
                # earlier ones are in the same box, which "Show N more" opens.
                entries = recalled["notes"]["items"]
                box = "section[data-group=notes]"
                more = page.locator(f"{box} button.graph-more")
                if await more.count():
                    await more.click()
                    await expect(more).to_have_text("Show fewer")
                earlier = await refs_in(
                    page, f"{box} p.graph-gbox-sub + ul button.graph-node.n-note"
                )
                everything = await refs_in(page, f"{box} button.graph-node.n-note")
                assert [r for r in everything if r not in earlier] == [
                    note_ref(one) for one in entries
                ], where
                assert earlier == [note_ref(one) for one in recalled["past_notes"]["items"]], where
                decided = [one["note"]["decided"] for one in entries]
                assert decided == sorted(decided, reverse=True), where
                for one in entries:
                    node = page.locator(f"button.graph-node[data-ref='{note_ref(one)}']")
                    confirmed = one["note"]["status"] == "confirmed"
                    # Never shown as confirmed unless a person confirmed it, and no note is a
                    # "Decision" of a kind of its own.
                    await expect(node.locator(".n-badge.ok")).to_have_count(int(confirmed))
                    await expect(node.locator(".n-badge.warn")).to_have_count(int(not confirmed))
                    await expect(node.locator(".n-badge:has-text('Decision')")).to_have_count(0)
                    via = one["inherited_from"]
                    await expect(node.locator(".n-via")).to_have_count(int(bool(via)))
                    if via:
                        await says_via(node.locator(".n-via"), client, via)
                # The project-wide notes apply to everything, so they are one line that opens.
                wide = [note_ref(one) for one in recalled["project_wide_notes"]["items"]]
                assert wide == [f"note:{world['dw']}"], where
                toggle = page.locator("#graph-wide-toggle")
                await expect(toggle).to_have_attribute("aria-expanded", "false")
                await toggle.click()
                await expect(toggle).to_have_attribute("aria-expanded", "true")
                assert (
                    await refs_in(
                        page, "ul[aria-label='Project-wide notes'] button.graph-node.n-note"
                    )
                    == wide
                ), where
                looked += 1
            # A neighbour that is work is looked around in its turn; a note or a criterion opens
            # its own page, in the Graph, and Back is the picture as it was left.
            await around_open(w, s1)
            around = f".graph-around[data-focus='{s1}']"
            await page.locator(
                f"{around} section[data-group=down] button.graph-node[data-ref='{t1}']"
            ).click()
            await expect(page.locator(f".graph-around[data-focus='{t1}']")).to_be_visible(
                timeout=SLOW
            )
            looks_around = (
                f"focus={t1}" in page.url.replace("%3A", ":") and "#/item" not in page.url
            )
            leads = []
            for ref, group in ((f"note:{world['n1']}", "notes"), (f"rule:{world['r1']}", "reqs")):
                await around_open(w, s1)
                left = await page.evaluate("location.hash")
                await page.locator(
                    f"{around} section[data-group={group}] button.graph-node[data-ref='{ref}']"
                ).click()
                await expect(page.locator(f"#item-page[data-ref='{ref}']")).to_be_visible(
                    timeout=SLOW
                )
                leads.append(unquote(page.url).endswith(f"#/item?item={ref}&from=graph"))
                await page.go_back()
                await expect(page.locator(around)).to_be_visible(timeout=SLOW)
                leads.append(await page.evaluate("location.hash") == left)
            assert w.problems == [], w.problems
    held, detail = checked(
        f"{looked} items compared with recall",
        five_items_looked_at=looked == 5,
        work_neighbour_looked_around=looks_around,
        note_and_criterion_open_and_return=all(leads) and len(leads) == 4,
    )
    proof(
        "M30",
        "Around draws, for an epic, a story, a task and an acceptance criterion, the very notes "
        "recall returns: in force in recall's order with the decided ones first, the earlier ones "
        "in the group that 'Show N more' opens, each marked Confirmed only when a person "
        "confirmed it and saying 'via' the story or epic it comes down from, and the project-wide "
        "notes as one line that opens; a task is looked around in its turn, and a note or a "
        "criterion opens its own page with Back bringing the picture back",
        held,
        detail,
    )


async def around_fits_the_tab() -> None:
    async with fresh_project(URL) as (client, board, project):
        world = await busy_world(client, "fits")
        epic = world["epic"]
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await page.set_viewport_size({"width": 1280, "height": 720})
            await around_open(w, epic)
            await page.wait_for_load_state("networkidle")
            await expect(page.locator(".graph-ego-panel.is-fit")).to_be_visible()
            found = await around_measured(page)
            few = []
            for group in ("down", "sim", "notes"):
                shown = await page.locator(
                    f"section[data-group={group}] button.graph-node:not(.n-wide)"
                ).count()
                few.append(1 <= shown <= 4)
            # "Show N more" says how many are left and makes the group grow where it is, so that
            # no card covers another and the page still does not scroll.
            box = "section[data-group=sim]"
            closed = await refs_in(page, f"{box} button.graph-node")
            total = int(await page.locator(f"{box} .graph-gbox-count").inner_text())
            more = page.locator(f"{box} button.graph-more")
            await expect(more).to_have_text(f"Show {total - len(closed)} more")
            await more.click()
            await expect(more).to_have_text("Show fewer")
            opened = await refs_in(page, f"{box} button.graph-node")
            await page.wait_for_timeout(400)
            overlapping = await page.evaluate(CARDS_OVERLAPPING)
            grown = await page.evaluate(AROUND_FITS)
            await more.click()
            assert w.problems == [], w.problems
    held, detail = checked(
        f"smallest text {found['smallest']:.1f} px, scale {found['scale']:.2f}, "
        f"{len(closed)} of {total} shown",
        no_page_scroll=found["scroll"] <= found["height"],
        inside_the_window=found["bottom"] <= found["height"] and found["right"] <= found["width"],
        no_sideways_scroll=found["sideways"] <= found["width"],
        readable=found["smallest"] >= 11,
        a_few_entries=all(few),
        all_the_rest_opens=len(opened) == total and closed == opened[: len(closed)],
        no_card_covers_another=overlapping == 0,
        still_no_scroll=grown["scroll"] <= grown["height"],
    )
    proof(
        "M38",
        "Around is fitted to its tab: at 1280 by 720 the page does not scroll down or sideways, "
        "the whole picture is inside the window, its smallest text is at least 11 pixels as "
        "drawn, each group shows a few entries, and 'Show N more' opens all the rest where the "
        "group is, with no card covering another",
        held,
        detail,
    )


# -- Order: the plan of one item, in phases and in chains ------------------------------------------


async def order_is_the_plan() -> None:
    async with fresh_project(URL) as (client, board, project):
        ids = await phase_world(client, "order")
        want = await phases_from_the_tools(client)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            sentence = "Choose an epic, story or task above to see its order."
            empty = page.locator("#graph-or-empty")
            # Nothing chosen: only the sentence, no first epic of its own choosing.
            await go(page, board, "#/graph?tab=order")
            await expect(empty).to_have_text(sentence, timeout=SLOW)
            asks = (
                await page.locator("#graph-view .graph-or").count() == 0
                and "focus=" not in page.url
            )
            # An epic chosen: its open tasks in phases, each task in the phase after the last of
            # the open tasks it needs first.
            await order_open(w, f"#/graph?tab=order&focus={ids['E']}")
            summary = await page.locator("p.graph-or-summary").inner_text()
            folded = await page.locator("#graph-or-done-toggle").inner_text()
            phases = []
            for step, refs in enumerate(want, start=1):
                await expect(page.locator("#graph-or-step")).to_have_text(
                    f"Phase {step} of {len(want)}"
                )
                phases.append(await refs_in(page, "#graph-or-phase li.graph-or-item") == refs)
                if step < len(want):
                    await page.locator("#graph-or-next").click()
            # The same plan as chains, one at a time, and last the items no link names.
            await order_open(w, f"#/graph?tab=order&focus={ids['E']}&mode=chains")
            await expect(page.locator("#graph-or-step")).to_have_text("Chain 1 of 2")
            first = sorted(await refs_in(page, ".graph-or-node"))
            await page.locator("#graph-or-next").click()
            await expect(page.locator("#graph-or-step")).to_have_text("Chain 2 of 2")
            second = sorted(await refs_in(page, ".graph-or-node"))
            await page.locator("#graph-or-next").click()
            await expect(page.locator("#graph-or-step")).to_have_text("Loose items (2)")
            loose = await refs_in(page, "#graph-or-loose li.graph-or-item")
            # Clearing the choice goes back to the sentence.
            await page.locator("#graph-focus-clear").click()
            await expect(empty).to_have_text(sentence, timeout=SLOW)
            assert w.problems == [], w.problems
    held, detail = checked(
        f"{len(want)} phases, {summary}",
        nothing_chosen_asks=asks,
        phases_equal_the_links=all(phases) and len(want) == 3,
        one_line=summary == "7 to do, 1 dropped not shown",
        done_folded=folded == "Done (1)",
        first_chain=first == sorted([ids["a"], ids["b"], ids["c"], ids["e"]]),
        second_chain=second == sorted([ids["h"], ids["i"]]),
        loose_last=loose == [ids["d"], ids["f"]],
    )
    proof(
        "M38",
        "Order with nothing chosen says only 'Choose an epic, story or task above to see its "
        "order' and does not pick an epic; with an epic chosen it lists its open tasks in the "
        "phases the links give (what each task needs first is in an earlier phase), says what is "
        "left in one line and folds the done work, and Chains shows one chain at a time with the "
        "loose items last",
        held,
        detail,
    )


# -- nothing of the removed taxonomy ---------------------------------------------------------------


async def no_removed_taxonomy() -> None:
    word = unique_word()
    async with fresh_project(URL) as (client, board, project):
        ids = await context_world(client, word, project)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            looked = 0
            for address in (
                "#/graph",
                f"#/graph?tab=around&focus={ids['s1']}",
                f"#/graph?tab=order&focus={ids['E']}",
            ):
                await go(page, board, address)
                await page.wait_for_load_state("networkidle")
                text = await page.locator("body").text_content() or ""
                assert not REMOVED_WORDS.search(text), address
                found: list[str] = await page.locator("#graph-view [data-kind]").evaluate_all(
                    "els => els.map(e => e.dataset.kind)"
                )
                assert set(found) <= {"epic", "story", "task", "rule", "note"}, (address, found)
                looked += 1
            for key in (
                ids["s1"],
                f"rule:{ids['r1']}",
                f"note:{ids['d1']}",
                f"note:{ids['n_old']}",
            ):
                await open_item(w, key, "graph")
                await page.wait_for_load_state("networkidle")
                assert not REMOVED_WORDS.search(await item_page(page).text_content() or ""), key
            await notes_open(page, board, context_list(ids["E"]))
            await page.locator("#add-note").click()
            await expect(dialog(page)).to_be_visible()
            note_dialog = REMOVED_WORDS.search(await dialog(page).text_content() or "")
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
        "M30",
        "no separate context store and no removed taxonomy appears in the Graph's three tabs, the "
        "page of any kind of record or the dialog that adds a note: only epics, stories, tasks, "
        "acceptance criteria and notes, read from the same tables the tools read",
        note_dialog is None and stores == "",
        f"{looked} tabs and 4 records read, no belief, principle or memory table",
    )


# -- one filter language ---------------------------------------------------------------------------


async def filters_are_one_language() -> None:
    async with connect(URL, the_token()) as client:
        ids = await twelve(client)
        epic = number_of(ids["epic"])
        async with browser_page(board_url(URL), token=the_token(), name=ADA) as w:
            page = w.page
            await w.open(f"#/board?q=epic:{epic}&show=both")
            everything = await all_nodes(client, query=f"epic:{epic}")
            await cards_are(page, {n["ref"] for n in everything if n["kind"] != "epic"})
            compared = 0
            for term in (
                f"epic:{epic} kind:task status:ready,in_progress -claimed priority:high "
                f"OR epic:{epic} kind:story",
                f"epic:{epic} claimed:{ids['ann']},{ids['bob']}",
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
            await w.open(f"#/lists?view=work&q={text.replace(' ', '+')}")
            rows = page.locator("#work-tree li[role=treeitem]")
            await expect(rows.first).to_be_visible(timeout=SLOW)
            keys = await rows.evaluate_all("els => els.map(e => e.dataset.key)")
            assert w.problems == [], w.problems
    proof(
        "M11",
        "one filter language: the Board's filter box gives the cards list_nodes gives for the "
        "same text, for each kind of term, a text that cannot be read is refused in list_nodes' "
        "words, and the Lists view's work list keeps the items the same filter asks for",
        wanted_here <= set(keys) and bool(wanted_here),
        f"{compared} filters compared",
    )


# -- the Roadmap -----------------------------------------------------------------------------------


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
            client, "add_link", source=ids["b"], target=ids["a"], link_type="needs_first"
        )  # fmt: skip
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await w.open(f"#/roadmap?q=epic:{number_of(epic)}")
            everyone = await roadmap_ids(page, 8)
            # The top bar names the page, so the page's own title is for a screen reader only.
            title = page.locator("#page h1")
            await expect(title).to_have_text("Roadmap")
            await expect(title).to_have_class(re.compile(r"\bsr-only\b"))
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
            says_so = (" ".join((await page.locator("#count").inner_text()).split())).endswith(
                "2 have no dates and are not drawn: see them in Lists."
            )
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
            conflict_words = await page.locator(".rm-label .chip.conflict").all_inner_texts()
            late_words = await bar_of(page, ids["c"]).get_attribute("aria-label") or ""
            late_notes = await page.locator(".rm-note.overdue").evaluate_all(
                "els => els.map(e => [e.dataset.ref, e.textContent.trim()])"
            )
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
            assert w.problems == [], w.problems
    held, detail = checked(
        f"{line['ppd']:g} pixels a day, today {line['today']}",
        eight_bars=everyone == {epic, story, *ids.values()},
        at_the_pixels=where_right,
        worked_out=worked_out == [("true", day(-20), day(30))] * 2,
        undated_left_off=all(left_off),
        says_so=says_so,
    )
    proof(
        "M11",
        "the Roadmap draws each of six bars at the pixels its dates give, the epic and story as "
        "worked-out spans, and leaves off the two items that have no dates, saying so and leading "
        "to them in Lists; the page's own title is for a screen reader only",
        held,
        detail,
    )
    held, detail = checked(
        f"{', '.join(conflict_words)}; {late_notes[0][1] if late_notes else 'nothing late'}",
        arrow=arrows == [(ids["a"], ids["b"])],
        conflict=conflicts == [ids["b"]]
        and any("Schedule conflict" in text for text in conflict_words),
        late=late_notes == [[ids["c"], "10 days late"]] and "10 days late" in late_words,
    )
    proof(
        "M11",
        "the needs_first link is an arrow from the prerequisite, the schedule conflict is on the "
        "item that starts too early, and late work is said in words beside its bar, only on the "
        "past item",
        held,
        detail,
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


async def the_roadmap_fills_the_window() -> None:
    async with fresh_project(URL) as (client, board, project):
        sparse = await crowded_world(client)
        async with browser_page(board, token=project.key, name=ADA) as w:
            page = w.page
            await w.open("#/roadmap")
            await page.set_viewport_size({"width": 1280, "height": 720})
            fits = []
            boxes = []
            for query, count in (("", 29), (f"epic:{number_of(sparse)} -kind:epic", 2)):
                await apply_filter(page, query)
                await roadmap_ids(page, count)
                found = await box_of(page)
                boxes.append(found)
                # The page is exactly the window: nothing scrolls but the box itself, which is
                # tall enough to reach the bottom even with two rows.
                fits.append(
                    found["page"] <= found["window"]
                    and found["pageWide"] <= found["windowWide"]
                    and found["window"] - 20 <= found["bottom"] <= found["window"]
                    and bool(found["scrolls"]) == (count > 4)
                )
            assert w.problems == [], w.problems
    proof(
        "M38",
        "the Roadmap fills the window: at 1280 by 720 the page does not scroll, the box with its "
        "grid reaches the bottom of the window with 29 rows and with two, and only the box "
        "scrolls when there are more rows than it holds",
        fits == [True, True],
        "29 rows and 2 rows" if fits == [True, True] else str(boxes),
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
            actor = signed_in_name(board)
        (first,) = await updates(client, moved)
        (second,) = await updates(client, ended)
        proof(
            "M11",
            "dragging a Roadmap bar moves both dates and dragging an end moves one, each a "
            "versioned change that get_node and the history show with the person and the versions",
            await dates(client, moved) == (day(8), day(17))
            and (first["actor"], first["before"]["version"], first["after"]["version"])
            == (actor, 1, 2)
            and first["after"] == {"start_on": day(8), "target_on": day(17), "version": 2}
            and await dates(client, ended) == (day(4), day(11))
            and (second["actor"], second["after"]) == (actor, {"target_on": day(11), "version": 2}),
            f"{day(3)}..{day(12)} -> {day(8)}..{day(17)}",
        )
        proof(
            "M11",
            "dragging a bar an agent changed meanwhile is refused with the stale-version message "
            "and the bar goes back; a start dragged past the target is refused with the MCP "
            "message",
            _alike(stale_words, said_stale)
            and _alike(wrong_words, said_wrong)
            and await dates(client, stale) == (day(1), day(2))
            and await dates(client, wrong) == (day(6), day(16))
            and [e["actor"] for e in await updates(client, stale)] == ["tests"]
            and await updates(client, wrong) == [],
            clip(wrong_words, 90),
        )


def _alike(board_words: str, mcp_words: str) -> bool:
    """The board said what the tool says (plain raises when it does not)."""
    plain(board_words, mcp_words)
    return True


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
            actor = signed_in_name(board)
        history = await updates(client, keyed)
        proof(
            "M11",
            "keys shift a bar by a day or a week (three presses are one change), each shown by "
            "get_node and the history",
            await dates(client, keyed) == (day(-1), day(8))
            and [(e["actor"], e["after"]["version"]) for e in history] == [(actor, 2), (actor, 3)],
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
        await make_note(client, f"About the kept task {word}", "Said in review", nodes=[task])
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
            await first.page.locator("#global-search").press("Enter")
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
                its_page = trail[0] == "Roadmap" and trail[-1].startswith(code_of(task))
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
