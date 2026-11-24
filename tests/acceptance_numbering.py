"""Chapter 17 of the acceptance scenario: numbering (M19, and its pages in M20). Epics, stories and
tasks are numbered on their own, and each is named by its kind and number (`epic:3`, `story:12`,
`task:40`, shown as E-3, S-12 and T-40) in every tool, answer, address, export and database column;
a story has a page, and an epic, story or task has previous and next.

The chapter makes its own account, project and key, so it needs nothing from the chapters before
it. A real MCP client makes three epics, four stories and five tasks and sees each kind count from
1; a second project starts again; no answer or database column holds an integer id; a bad
reference, one that names nothing and one of the wrong kind are refused in plain words; the
board's own New item goes on from each kind's highest number; the Board, the Lists view's Work
list and the Roadmap show the codes the server's references give; `#/item?item=story:1` is the
page of a story, whose table of tasks is what `list_nodes` lists and whose previous and next go
through the stories of its epic; and an export loads into a second copy of Vrum with every
number, which then goes on from the highest of each kind."""

import re
import tempfile
from pathlib import Path
from typing import Any

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import expect

from tests.acceptance_board import create_here
from tests.acceptance_scenario import Failed, chapter, clip, proof
from tests.board_helpers import (
    SLOW,
    Watched,
    board_url,
    browser_page,
    item_rows,
)
from tests.helpers import (
    all_nodes,
    code_of,
    connect,
    day,
    export_everything,
    kind_of,
    number_of,
    ok,
    refused,
    run_sql,
    unique_word,
)
from tests.second_stack import SecondStack
from tests.test_node_numbering_integration import (
    EVERYTHING,
    GONE_NAMES,
    NAMES_A_NODE,
    NUMBERED,
    create,
    files_of,
    id_problems,
    newest_event,
    next_numbers,
    numbered_project,
    script,
    sql,
    whole_project,
)
from tests.world import Project, make_project, register

NOT_A_REFERENCE = "must name an epic, story or task by its kind and number"


def words(text: str) -> str:
    return " ".join(text.split())


async def numbering() -> None:
    chapter("Numbering: epics, stories and tasks each count from 1, and a story has a page")
    word = unique_word()
    project = make_project(register(name=f"Numbering {word}"), f"Numbering {word}")
    try:
        async with connect(project.mcp_url, project.key) as client:
            await the_numbers(client, word)
            await no_integer_ids(project, client, word)
            await refusals(project, client)
            await dates_for_the_roadmap(client)
            async with browser_page(board_url(project.mcp_url, project), token=project.key) as w:
                await the_board_goes_on(w, client, word)
                await codes_equal_references(w, client)
                await addresses(w, client)
                await the_page(w, client)
            await moving(project, client)
    except BrowserError as error:
        raise Failed(
            f"M19: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- the numbers, the ids that are gone, and what is refused -------------------------------------


async def the_numbers(client: Client, word: str) -> None:
    made = await numbered_project(client, word)
    three = [await ok(client, "get_node", node=ref) for ref in ("epic:1", "story:1", "task:1")]
    other = make_project(register(), f"Other {word}")
    async with connect(other.mcp_url, other.key) as elsewhere:
        starts_again = [
            await create(elsewhere, "epic", f"Other epic {word}"),
            await create(elsewhere, "story", f"Other story {word}", "epic:1"),
            await create(elsewhere, "task", f"Other task {word}", "story:1"),
        ]
    proof(
        "M19",
        "in a new project, 3 epics, 4 stories and 5 tasks made through MCP are numbered 1 to 3, "
        "1 to 4 and 1 to 5: epic:1, story:1 and task:1 are three different items, and a second "
        "project starts again at 1",
        made == [ref for _, _, ref in NUMBERED]
        and [(n["kind"], n["number"]) for n in three] == [("epic", 1), ("story", 1), ("task", 1)]
        and len({n["title"] for n in three}) == 3
        and [n["ref"] for n in await all_nodes(client)] == EVERYTHING
        and starts_again == ["epic:1", "story:1", "task:1"],
        ", ".join(made),
    )


async def no_integer_ids(project: Project, client: Client, word: str) -> None:
    exported = await export_everything(client)
    answers = [
        await ok(client, "get_node", node="task:5"),
        {"items": await all_nodes(client)},
        await ok(client, "changes_since", limit=200),
        await ok(client, "find_mentions"),
        await ok(client, "search", query=word, mode="words", limit=50),
        await ok(client, "recall", node="story:1"),
        await ok(client, "coverage_report"),
        *({"items": exported[section]} for section in ("nodes", "links", "rules", "events")),
    ]
    problems = [problem for answer in answers for problem in id_problems("answer", answer)]
    gone = ", ".join(f"'{name}'" for name in sorted(GONE_NAMES))
    renamed = run_sql(
        "SELECT table_name || '.' || column_name FROM information_schema.columns WHERE "
        f"table_schema = 'public' AND column_name IN ({gone})",
        plain=True,
        project=None,
    ).stdout.strip()
    holding_refs = run_sql(NAMES_A_NODE, plain=True, project=None).stdout.splitlines()
    odd_events = sql(
        "SELECT count(*) FILTER (WHERE node !~ '^(epic|story|task):[1-9][0-9]*$') "
        f"FROM events WHERE project_id = {project.id}",
        project,
    )
    proof(
        "M19",
        "no answer (a node, the whole list, the change feed, mentions, search, recall, coverage, "
        "the export's lists) and no database column holds an integer node id: a node has a ref, "
        "a kind and a number, the 12 columns that name a node hold its ref, and every event "
        "names nodes by ref",
        not problems and renamed == "" and len(holding_refs) == 12 and odd_events == "0",
        f"{len(answers)} answers read, none with an id",
    )


async def refusals(project: Project, client: Client) -> None:
    before = await whole_project(client, project)
    latest = await newest_event(client)
    cases: list[tuple[str, dict[str, Any], str]] = [
        ("get_node", {"node": "12"}, NOT_A_REFERENCE),
        ("get_node", {"node": "story:0"}, NOT_A_REFERENCE),
        ("get_node", {"node": "sprint:3"}, NOT_A_REFERENCE),
        ("get_node", {"node": "story:9999"}, "There is no story 9999 in this project"),
        ("claim_node", {"actor": "a", "node": "epic:1"}, "Only stories and tasks can be claimed"),
        (
            "create_node",
            {"actor": "a", "kind": "story", "title": "N", "parent": "story:1"},
            "A story belongs under an epic, but story:1 is a story",
        ),
        (
            "add_link",
            {"actor": "a", "source": "task:1", "target": "task:99", "link_type": "needs_first"},
            "There is no task 99 in this project",
        ),
    ]
    said = [(await refused(client, tool, **arguments), says) for tool, arguments, says in cases]
    proof(
        "M19",
        "a bad reference (12, story:0, sprint:3), one that names nothing and one of the wrong "
        "kind are refused in plain words by the tools that take one, and nothing is changed",
        all(says in message for message, says in said)
        and await whole_project(client, project) == before
        and await newest_event(client) == latest,
        f'"{clip(said[0][0], 100)}"',
    )


async def dates_for_the_roadmap(client: Client) -> None:
    """Dates on three of the items: the Roadmap draws nothing without them."""
    for step, ref in enumerate(("epic:1", "story:1", "task:1"), start=1):
        node = await ok(client, "get_node", node=ref)
        await ok(
            client,
            "update_node",
            actor="tester",
            node=ref,
            expected_version=node["version"],
            start_on=day(step),
            target_on=day(step + 5),
        )


# -- the board -----------------------------------------------------------------------------------


async def the_board_goes_on(w: Watched, client: Client, word: str) -> None:
    page = w.page
    await w.open("#/board")
    await expect(page.locator("#new-item")).to_be_visible(timeout=SLOW)
    epic = await create_here(page, "epic", f"Board epic {word}")
    story = await create_here(page, "story", f"Board story {word}", epic)
    task = await create_here(page, "task", f"Board task {word}", story)
    shown = words(await page.locator("#item-title").inner_text())
    made = await ok(client, "get_node", node=task)
    proof(
        "M19",
        "the Board's own New item goes on from each kind's own highest number: after 3 epics, "
        "4 stories and 5 tasks it makes epic 4, story 5 and task 6, the new task's page names it "
        "T-6, and the server has it under story:5",
        (epic, story, task) == ("epic:4", "story:5", "task:6")
        and shown == f"T-6 Board task {word}"
        and made["parent"] == "story:5",
        f"{epic}, {story}, {task}",
    )


# The code each page shows for each item it draws, by the item's reference.
ON_THE_BOARD = """() => Object.fromEntries(
    [...document.querySelectorAll('.card[data-ref], .lane[data-ref]')]
        .map((e) => [e.dataset.ref, (/\\b[EST]-\\d+\\b/.exec(e.innerText) || [''])[0]]))"""
IN_THE_LIST = """() => Object.fromEntries(
    [...document.querySelectorAll('#work-tree li[role=treeitem]')]
        .map((e) => [e.dataset.ref, e.querySelector(':scope > .gv-row .id').innerText.trim()]))"""
ON_THE_ROADMAP = """() => Object.fromEntries(
    [...document.querySelectorAll('.rm-label[data-ref]')]
        .map((e) => [e.dataset.ref, (/\\b[EST]-\\d+\\b/.exec(e.innerText) || [''])[0]]))"""


async def codes_equal_references(w: Watched, client: Client) -> None:
    page = w.page
    everything = await all_nodes(client)
    wanted = {node["ref"]: code_of(node["ref"]) for node in everything}
    epics = sum(1 for node in everything if node["kind"] == "epic")
    await w.open("#/board?show=both")
    await expect(page.locator(".lane")).to_have_count(epics, timeout=SLOW)
    await expect(page.locator(".card")).to_have_count(len(everything) - epics, timeout=SLOW)
    on_board = await page.evaluate(ON_THE_BOARD)
    await w.open("#/lists?view=work")
    rows = page.locator("#work-tree li[role=treeitem]")
    await expect(rows).to_have_count(len(everything), timeout=SLOW)
    in_list = await page.evaluate(IN_THE_LIST)
    await w.open("#/roadmap")
    await expect(page.locator(".rm-bar")).to_have_count(3, timeout=SLOW)
    on_roadmap = await page.evaluate(ON_THE_ROADMAP)
    proof(
        "M19",
        "the Board, the Lists view's Work list and the Roadmap show E-, S- and T- codes that "
        "equal the server's references (epic:4 is E-4, story:5 is S-5, task:6 is T-6): the "
        "Board and the list for every item the server lists, the Roadmap for the three that "
        "have dates",
        on_board == in_list == wanted
        and len(wanted) == 15
        and on_roadmap == {ref: wanted[ref] for ref in ("epic:1", "story:1", "task:1")},
        f"{len(wanted)} items on the Board and in the list, {len(on_roadmap)} on the Roadmap",
    )


async def addresses(w: Watched, client: Client) -> None:
    page = w.page
    story = await ok(client, "get_node", node="story:1")
    await w.open("#/item?item=story:1")
    await expect(page.locator("#item-title")).to_contain_text("S-1 ", timeout=SLOW)
    opened = words(await page.locator("#item-title").inner_text())
    await w.open("#/board?show=both")
    await expect(page.locator(".card[data-ref='story:1']")).to_be_visible(timeout=SLOW)
    await page.locator(".card[data-ref='story:1'] .card-title a").click()
    await expect(page.locator("#item-page")).to_have_attribute("data-ref", "story:1", timeout=SLOW)
    clicked = page.url
    await w.open("#/item?item=node:1")
    await expect(page.locator("#item-page")).to_contain_text("does not name", timeout=SLOW)
    nothing_shown = await page.locator("#item-page .card-detail").count() == 0
    proof(
        "M19",
        "an address names an item by kind and number: item=story:1 opens the page of S-1 with the "
        "server's title, choosing a card on the Board writes item=story:1 (and the Board as where "
        "it came from) in the address, and the old node:1 says in plain words that it names "
        "nothing",
        opened == f"S-1 {story['title']}"
        and clicked.endswith("#/item?item=story:1&from=board")
        and nothing_shown,
        f'"{opened}"',
    )


# -- the page of a story -------------------------------------------------------------------------


async def the_page(w: Watched, client: Client) -> None:
    page = w.page
    story = await ok(client, "get_node", node="story:1")
    story_tasks = await all_nodes(client, parent="story:1")
    await w.open("#/item?item=story:1")
    await expect(page.locator("#item-details .card-detail")).to_be_visible(timeout=SLOW)
    title = words(await page.locator("#item-title").inner_text())
    count = words(await page.locator("#item-count").inner_text())
    rows = await item_rows(page)
    stories = await all_nodes(client, parent="epic:1")
    await w.open("#/item?item=epic:1")
    await expect(page.locator("#item-details .card-detail")).to_be_visible(timeout=SLOW)
    await expect(page.locator("#item-table")).to_be_visible(timeout=SLOW)
    epic_rows = await item_rows(page)
    progress = [
        words(
            await page.locator(f"#item-table tr[data-ref='{s['ref']}'] .cell-progress").inner_text()
        )
        for s in stories
    ]
    done = [
        f"{s['progress_done']} of {s['progress_total']} done" if s["progress_total"] else ""
        for s in stories
    ]
    proof(
        "M19",
        "the page of a story, #/item?item=story:1, shows its code and title and a table of its "
        "tasks that is exactly what list_nodes lists under it, with the count; the page of an "
        "epic lists its stories with the progress the server gives",
        title == f"S-1 {story['title']}"
        and rows == [t["ref"] for t in story_tasks] == ["task:1", "task:5"]
        and count == "2"
        and epic_rows == [s["ref"] for s in stories] == ["story:1", "story:4"]
        and progress == done,
        f"{title}: {', '.join(rows)}",
    )

    # Previous and next stay among its siblings, and the view the person came from goes along.
    siblings = {
        "story:1": [s["ref"] for s in sorted(stories, key=lambda n: n["number"])],
        "epic:1": [n["ref"] for n in await all_nodes(client, query="kind:epic")],
        "task:1": [t["ref"] for t in sorted(story_tasks, key=lambda n: n["number"])],
    }
    seen: dict[str, str] = {}
    at_the_ends = []

    async def is_at(ref: str) -> None:
        await expect(page.locator("#item-page")).to_have_attribute("data-ref", ref, timeout=SLOW)

    for start in ("story:1", "epic:1", "task:1"):
        row = siblings[start]
        await w.open(f"#/item?item={start}&from=graph")
        await is_at(start)
        await expect(page.locator("#item-position")).to_have_text(f"1 of {len(row)}", timeout=SLOW)
        at_the_ends.append(await page.locator("#item-prev").is_disabled())
        await page.locator("#item-next").click()
        await is_at(row[1])
        seen[start] = words(await page.locator("#item-position").inner_text())
        await page.keyboard.press("[")
        await is_at(start)
    await w.open("#/item?item=story:4&from=graph")
    await is_at("story:4")
    at_the_ends.append(await page.locator("#item-next").is_disabled())
    await page.keyboard.press("]")
    await expect(page.locator("#item-next")).to_be_disabled()
    kept = page.url.endswith("#/item?item=story:4&from=graph")
    in_the_graph = words(await page.locator("nav.crumbs .crumb-view").inner_text()) == "Graph"
    proof(
        "M20",
        "the page of an epic, a story and a task has previous and next among its siblings, the "
        "epics of the project, the stories of its epic and the tasks of its story, in the order of "
        "their numbers: it says its place (1 of 2 for S-1 and T-1, 1 of 4 for E-1), Next goes to "
        "the next by number and the [ key back, the buttons are off at the ends, and the view the "
        "person came from (from=graph) is still in the path after every step",
        seen
        == {
            "story:1": f"2 of {len(siblings['story:1'])}",
            "epic:1": f"2 of {len(siblings['epic:1'])}",
            "task:1": f"2 of {len(siblings['task:1'])}",
        }
        and siblings["story:1"] == ["story:1", "story:4"]
        and siblings["task:1"] == ["task:1", "task:5"]
        and all(at_the_ends)
        and kept
        and in_the_graph,
        f"{len(siblings['epic:1'])} epics, {len(siblings['story:1'])} stories under E-1",
    )

    await w.open("#/board?epic=1&show=both")
    await expect(page.locator(".card[data-ref='story:1']")).to_be_visible(timeout=SLOW)
    await page.locator(".card[data-ref='story:1'] .card-title a").click()
    await is_at("story:1")
    by_card = page.url.endswith("#/item?item=story:1&from=board")
    await w.open("#/lists?view=work&epic=1")
    tree = page.locator("#work-tree")
    await expect(tree.locator("li[data-key='story:1']")).to_be_visible(timeout=SLOW)
    await tree.locator("li[data-key='story:1'] > .gv-row a.gv-title").click()
    await is_at("story:1")
    by_title = page.url.endswith("#/item?item=story:1&from=lists")
    await w.open("#/lists?view=work&epic=1")
    await expect(tree.locator("li[data-key='task:1']")).to_be_visible(timeout=SLOW)
    await tree.locator("li[data-key='task:1'] > .gv-row a.gv-title").click()
    await is_at("task:1")
    task_has_a_page = page.url.endswith("#/item?item=task:1&from=lists")
    proof(
        "M19",
        "a card on the Board and the title of a story or a task in the Lists view's Work list "
        "each open that item's own page, and the address says which view it came from "
        "(from=board, from=lists)",
        by_card and by_title and task_has_a_page and w.problems == [],
        "story:1's page from the Board and from Lists, task:1's from Lists",
    )


# -- moving the project --------------------------------------------------------------------------


async def moving(project: Project, client: Client) -> None:
    original = await export_everything(client)
    want = next_numbers(original)
    stack = SecondStack()
    try:
        with tempfile.TemporaryDirectory() as folder:
            exported, copy = Path(folder) / "export.json", Path(folder) / "markdown"
            wrote = script("export_graph.py", project.key, project.mcp_url, exported)
            copied = script("export_markdown.py", project.key, project.mcp_url, copy)
            if wrote.returncode or copied.returncode:
                raise Failed(f"M19: the export scripts failed: {wrote.stderr}{copied.stderr}")
            stack.up()
            loaded = stack.python("import_graph.py", stack.url, exported)
            if loaded.returncode:
                raise Failed(f"M19: the import failed: {loaded.stdout}{loaded.stderr}")
            async with stack.client() as second:
                same = await export_everything(second) == original
                new_epic = await create(second, "epic", "After the import")
                new_story = await create(second, "story", "After the import", new_epic)
                new_task = await create(second, "task", "After the import", new_story)
            files = files_of(copy)
    finally:
        stack.close()
    node_files = [name for name in files if name.startswith(("epics/", "stories/", "tasks/"))]
    named = {re.match(r"[a-z]+/([a-z]+-\d+)-", name).group(1) for name in node_files}  # type: ignore[union-attr]
    expected = {f"{n['kind']}-{n['number']}" for n in original["nodes"]}
    proof(
        "M19",
        "an export loads into a second copy of Vrum with every number, link and "
        "event as it was; the next epic, story and task go on from the highest number of their "
        "own kind; and the Markdown copy has one file per item named by kind and number",
        same
        and {kind_of(r): number_of(r) for r in (new_epic, new_story, new_task)} == want
        and len(node_files) == len(original["nodes"])
        and named == expected,
        f"next numbers {want['epic']}, {want['story']}, {want['task']}; {len(files)} files",
    )
