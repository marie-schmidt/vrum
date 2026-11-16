"""M11 against the running containers: the Table and the Outline, driven in a real headless browser
and checked against a real MCP client (see ./check.sh). The rows the board shows are compared with
what list_nodes gives for the same filter text, and what a person does is read back through the
tools. Every filter starts with `epic:<id>` so that a test sees only the items it made."""

import asyncio
import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Page, expect

from tests.board_helpers import (
    SLOW,
    apply_filter,
    board_url,
    browser_page,
    groups,
    hold_back_news,
    shown_ids,
    sort_by,
)
from tests.helpers import all_nodes, day, make_rule, ok, refused, run_sql, the_token, unique_word

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

PRIORITIES = ["low", "normal", "high", "urgent"]


@pytest.fixture
def board(server_url: str) -> str:
    return board_url(server_url)


async def made(
    client: Client, kind: str, title: str, parent: int | None = None, **more: Any
) -> int:
    fields: dict[str, Any] = {"actor": "agent-1", "kind": kind, "title": title, **more}
    if parent:
        fields["parent_id"] = parent
    return int((await ok(client, "create_node", **fields))["id"])


def plain(board_words: str, mcp_words: str) -> None:
    assert board_words.strip() and not board_words.startswith("Error executing tool")
    assert mcp_words.endswith(board_words.strip()), (board_words, mcp_words)


async def twelve(client: Client) -> dict[str, int]:
    """One epic, three stories and eight tasks that differ in status, priority, who holds them,
    dates and what they wait for: twelve items."""
    word = unique_word()
    ids: dict[str, int] = {"epic": await made(client, "epic", f"Twelve {word}")}
    ids["s1"] = await made(client, "story", f"Billing {word}", ids["epic"], priority="high")
    ids["s2"] = await made(client, "story", f"Portal {word}", ids["epic"])
    ids["s3"] = await made(client, "story", f"Reports {word}", ids["epic"], priority="urgent")
    for name, story, priority, extra in (
        ("t1", "s1", "urgent", {}),
        ("t2", "s1", "high", {}),
        ("t3", "s1", "low", {}),
        ("t4", "s2", "high", {}),
        ("t5", "s2", "normal", {}),
        ("t6", "s2", "high", {"target_on": day(-2)}),
        ("t7", "s3", "normal", {}),
        ("t8", "s3", "high", {"start_on": day(1), "target_on": day(9)}),
    ):
        ids[name] = await made(
            client, "task", f"Task {name} {word}", ids[story], priority=priority, **extra
        )
    await ok(client, "claim_node", actor="ann", node_id=ids["t1"])
    await ok(
        client,
        "update_node",
        actor="a",
        node_id=ids["t1"],
        expected_version=1,
        status="in_progress",
    )
    await ok(client, "update_node", actor="a", node_id=ids["t3"], expected_version=1, status="done")
    await ok(
        client, "update_node", actor="a", node_id=ids["t4"], expected_version=1, status="blocked"
    )
    await ok(client, "claim_node", actor="bob", node_id=ids["t7"])
    await ok(
        client,
        "add_link",
        actor="a",
        source_id=ids["t5"],
        target_id=ids["t2"],
        link_type="needs_first",
    )
    return ids


async def eventually(client: Any, node_id: int, wanted: dict[str, Any]) -> None:
    """Wait (up to ten seconds) until the graph holds these values for a node: a person's edit
    reaches the server a moment after the cell shows it."""
    held: dict[str, Any] = {}
    for _ in range(40):
        held = await ok(client, "get_node", node_id=node_id)
        if all(held[name] == value for name, value in wanted.items()):
            return
        await asyncio.sleep(0.25)
    raise AssertionError(f"the graph never held {wanted}: { ({n: held[n] for n in wanted}) }")


# -- 1. the Table is what list_nodes gives -------------------------------------------------------


async def test_the_table_sorts_groups_and_filters_as_list_nodes_does_and_refuses_a_bad_query(
    client: Client, board: str
) -> None:
    ids = await twelve(client)
    epic = ids["epic"]
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open(f"#/table?q=epic:{epic}")
        everything = await all_nodes(client, query=f"epic:{epic}")
        assert len(everything) == 12
        assert sorted(await shown_ids(page, 12)) == sorted(n["id"] for n in everything)

        # The filter of the spec, which agents give as `query`: the same ids, and the same text.
        text = (
            f"epic:{epic} kind:task status:ready,in_progress -claimed priority:high "
            f"OR epic:{epic} kind:story"
        )
        wanted = await all_nodes(client, query=text)
        assert 0 < len(wanted) < 12
        await apply_filter(page, text)
        assert sorted(await shown_ids(page, len(wanted))) == sorted(n["id"] for n in wanted)
        assert f"q=epic:{epic}" in page.url and "OR" in page.url

        # Sorting: by priority up, then down, then back to the order of ids.
        await apply_filter(page, f"epic:{epic}")
        await shown_ids(page, 12)
        by_priority = sorted(everything, key=lambda n: (PRIORITIES.index(n["priority"]), n["id"]))
        await sort_by(page, "priority", "ascending")
        assert await shown_ids(page, 12) == [n["id"] for n in by_priority]
        await sort_by(page, "priority", "descending")
        descending = sorted(everything, key=lambda n: (-PRIORITIES.index(n["priority"]), n["id"]))
        assert await shown_ids(page, 12) == [n["id"] for n in descending]
        await sort_by(page, "priority", "none")
        assert await shown_ids(page, 12) == [n["id"] for n in everything]
        # Dates sort with the items that have none last, whichever way.
        await sort_by(page, "target", "ascending")
        dated = [n for n in everything if n["target_on"]]
        undated = [n for n in everything if not n["target_on"]]
        order = await shown_ids(page, 12)
        assert order[: len(dated)] == [
            n["id"] for n in sorted(dated, key=lambda n: (n["target_on"], n["id"]))
        ]
        assert order[len(dated) :] == [n["id"] for n in undated]
        await sort_by(page, "target", "descending")
        order = await shown_ids(page, 12)
        by_id = sorted(dated, key=lambda n: n["id"])
        assert order[: len(dated)] == [
            n["id"] for n in sorted(by_id, key=lambda n: n["target_on"], reverse=True)
        ]
        assert order[len(dated) :] == [n["id"] for n in undated]

        # Grouping: each group holds what list_nodes says has that value.
        await w.go(f"#/table?q=epic:{epic}&group=status")
        got = await groups(page)
        for status in {n["status"] for n in everything}:
            assert got[status] == [n["id"] for n in everything if n["status"] == status], status
        assert set(got) == {n["status"] for n in everything}
        await w.go(f"#/table?q=epic:{epic}&group=claimed")
        got = await groups(page)
        assert got["ann"] == [ids["t1"]] and got["bob"] == [ids["t7"]]
        assert sorted(got[""]) == sorted(n["id"] for n in everything if not n["claimed_by"])
        await w.go(f"#/table?q=epic:{epic}&group=story")
        got = await groups(page)
        assert got[str(ids["s1"])] == [ids["s1"], ids["t1"], ids["t2"], ids["t3"]]
        assert got["none"] == [epic]
        await w.go(f"#/table?q=epic:{epic}&group=priority&sort=-title")
        got = await groups(page)
        assert list(got) == ["urgent", "high", "normal", "low"]
        assert got["urgent"] == [
            n["id"]
            for n in sorted(
                (n for n in everything if n["priority"] == "urgent"),
                key=lambda n: n["title"].lower(),
                reverse=True,
            )
        ]

        # A query that cannot be read is refused in the words list_nodes uses, and the rows stay.
        bad = f"epic:{epic} colour:red"
        said = await refused(client, "list_nodes", query=bad)
        await w.go(f"#/table?q=epic:{epic}")
        await shown_ids(page, 12)
        await apply_filter(page, bad)
        problem = page.locator("#filter-problem")
        await expect(problem).to_contain_text("there is no field called 'colour'")
        plain(await problem.inner_text(), said)
        assert len(await page.locator("tr.item").all()) == 12
        assert w.problems == [], w.problems


# -- 2. several rows at once, each its own versioned change ---------------------------------------


async def test_several_rows_change_at_once_and_the_one_an_agent_changed_meanwhile_is_refused(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Bulk {word}")
    story = await made(client, "story", f"Story {word}", epic)
    t1, t2, t3, t4 = [await made(client, "task", f"Task {n} {word}", story) for n in range(4)]
    async with browser_page(board, token=the_token(), name="Ada Lovelace") as w:
        page = w.page
        await w.open(f"#/table?q=epic:{epic} kind:task")
        await shown_ids(page, 4)
        for id_ in (t1, t2, t3):
            await page.locator(f"tr.item[data-id='{id_}'] input[type=checkbox]").check()
        await expect(page.locator("#bulk-count")).to_have_text("3 rows chosen")

        # An agent changes the second while the three are chosen.
        await ok(
            client,
            "update_node",
            actor="agent-1",
            node_id=t2,
            expected_version=1,
            title=f"Agent was here {word}",
        )
        said = await refused(
            client, "update_node", actor="x", node_id=t2, expected_version=1, title="x"
        )
        await page.locator("#bulk-priority").select_option("urgent")
        await page.locator("#bulk-status").select_option("in_progress")
        await page.locator("#bulk-apply").click()

        result = page.locator("#bulk-result .bulk-result")
        await expect(result).to_contain_text("2 changed, 1 refused", timeout=SLOW)
        assert await result.locator("li[data-applied=true]").evaluate_all(
            "els => els.map(e => Number(e.dataset.id))"
        ) == [t1, t3]
        refusal = result.locator(f"li[data-id='{t2}'] .refusal-text")
        plain(re.sub(r"^not changed\. ", "", await refusal.inner_text()), said)

        for id_ in (t1, t3):
            node = await ok(client, "get_node", node_id=id_)
            assert (node["priority"], node["status"], node["version"]) == (
                "urgent",
                "in_progress",
                2,
            )
            update = (await ok(client, "node_history", node_id=id_))["items"][-1]
            assert (update["action"], update["actor"]) == ("update", "Ada Lovelace")
            assert update["before"] == {"priority": "normal", "status": "new", "version": 1}
            assert update["after"] == {"priority": "urgent", "status": "in_progress", "version": 2}
        refused_node = await ok(client, "get_node", node_id=t2)
        assert (refused_node["priority"], refused_node["status"]) == ("normal", "ready")
        history = (await ok(client, "node_history", node_id=t2))["items"]
        assert [(e["action"], e["actor"]) for e in history] == [
            ("create", "agent-1"),
            ("update", "agent-1"),
        ]
        assert (await ok(client, "get_node", node_id=t4))["version"] == 1
        # The table shows what the graph now holds.
        await expect(page.locator(f"tr.item[data-id='{t1}'] td.priority select")).to_have_value(
            "urgent"
        )
        await expect(page.locator(f"tr.item[data-id='{t2}'] td.title input")).to_have_value(
            f"Agent was here {word}"
        )
        assert w.problems == [], w.problems


async def test_a_cell_is_edited_in_place_and_a_stale_cell_is_refused_and_goes_back(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Cells {word}")
    story = await made(client, "story", f"Story {word}", epic)
    task = await made(client, "task", f"Task {word}", story)
    other = await made(client, "task", f"Other {word}", story)
    async with browser_page(board, token=the_token(), name="Ada Lovelace") as w:
        page = w.page
        await w.open(f"#/table?q=epic:{epic} kind:task")
        await shown_ids(page, 2)
        row = page.locator(f"tr.item[data-id='{task}']")
        await row.locator("td.priority select").select_option("high")
        await expect(page.locator(".notice.done")).to_contain_text(f"Saved the priority of #{task}")
        await row.locator("td.status select").select_option("blocked")
        await row.locator("td.target input").fill(day(7))
        await row.locator("td.title input").fill(f"Renamed {word}")
        await row.locator("td.title input").press("Enter")
        await expect(row.locator("td.title input")).to_have_value(f"Renamed {word}")
        await eventually(
            client, task, {"title": f"Renamed {word}", "status": "blocked", "target_on": day(7)}
        )
        node = await ok(client, "get_node", node_id=task)
        assert (node["priority"], node["status"], node["target_on"], node["title"]) == (
            "high", "blocked", day(7), f"Renamed {word}",
        )  # fmt: skip
        assert all(
            e["actor"] in ("Ada Lovelace", "agent-1")
            for e in (await ok(client, "node_history", node_id=task))["items"]
        )
        # Clearing a date is a change too.
        await row.locator("td.target input").fill("")
        await expect(row.locator("td.target input")).to_have_value("")
        await eventually(client, task, {"target_on": None})

        # A change the board has not heard of: the cell is refused, and shows the agent's value.
        await hold_back_news(page)
        row2 = page.locator(f"tr.item[data-id='{other}']")
        await ok(
            client,
            "update_node",
            actor="agent-1",
            node_id=other,
            expected_version=1,
            priority="urgent",
        )
        said = await refused(
            client, "update_node", actor="x", node_id=other, expected_version=1, priority="low"
        )
        await row2.locator("td.priority select").select_option("low")
        refusal = page.locator(".notice.refusal .notice-text").first
        await expect(refusal).to_be_visible(timeout=SLOW)
        plain(await refusal.inner_text(), said)
        await expect(row2.locator("td.priority select")).to_have_value("urgent")
        assert (await ok(client, "get_node", node_id=other))["priority"] == "urgent"
        assert w.problems == [], w.problems


# -- 3. the Outline --------------------------------------------------------------------------------


async def tree_of(page: Page, epic: int) -> dict[str, Any]:
    """The branch of one epic as the page draws it: ids, kinds and the words of each row."""
    found: dict[str, Any] = await page.locator(f"li.node[data-id='{epic}']").evaluate(
        """(top) => {
            const read = (li) => ({
                id: Number(li.dataset.id), kind: li.dataset.kind,
                progress: (li.querySelector(':scope > .row .progress-line') || {})
                    .textContent || null,
                rules: [...li.querySelectorAll(':scope > ul > li.rule')]
                    .map(r => Number(r.dataset.rule)),
                children: [...li.querySelectorAll(':scope > ul > li.node')].map(read),
            });
            return read(top);
        }"""
    )
    return found


async def test_the_outline_is_list_nodes_as_a_tree_and_a_task_drags_to_another_story(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Outline {word}")
    s1 = await made(client, "story", f"First {word}", epic)
    s2 = await made(client, "story", f"Second {word}", epic)
    a, b, c = [await made(client, "task", f"Task {n} {word}", s1) for n in "abc"]
    d = await made(client, "task", f"Task d {word}", s2)
    await ok(client, "update_node", actor="x", node_id=a, expected_version=1, status="done")
    rule = await make_rule(client, f"A rule {word}", story_id=s1)
    await make_rule(client, f"A dropped rule {word}", story_id=s1, status="dropped")

    async def expected(parent: int | None) -> list[dict[str, Any]]:
        nodes = (
            await all_nodes(client, parent_id=parent)
            if parent
            else await all_nodes(client, kind="epic")
        )
        return [n for n in nodes if parent or n["id"] == epic]

    async def counts(node_id: int) -> str:
        node = await ok(client, "get_node", node_id=node_id)
        return (
            f"{node['progress_done']} of {node['progress_total']} task"
            + ("" if node["progress_total"] == 1 else "s")
            + " done"
        )

    async with browser_page(board, token=the_token(), name="Ada Lovelace") as w:
        page = w.page
        await w.open("#/outline")
        await expect(page.locator(f"li.node[data-id='{epic}']")).to_be_visible(timeout=SLOW)
        drawn = await tree_of(page, epic)
        stories = await expected(epic)
        assert [child["id"] for child in drawn["children"]] == [n["id"] for n in stories]
        for child in drawn["children"]:
            tasks = await all_nodes(client, parent_id=child["id"])
            assert [t["id"] for t in child["children"]] == [t["id"] for t in tasks]
            assert child["progress"] == await counts(child["id"])
        assert drawn["progress"] == await counts(epic)
        assert drawn["children"][0]["rules"] == [rule["id"]]  # and the dropped rule is not shown
        assert drawn["children"][1]["rules"] == []
        before_s1, before_s2 = await counts(s1), await counts(s2)
        assert before_s1 == "1 of 3 tasks done" and before_s2 == "0 of 1 task done"

        # Drag the done task to the other story: the same as a move through the tools.
        version = (await ok(client, "get_node", node_id=a))["version"]
        await page.drag_and_drop(
            f"li.node[data-id='{a}'] > .row", f"li.node[data-id='{s2}'] > .row"
        )
        await expect(
            page.locator(f"li.node[data-id='{s2}'] > ul > li.node[data-id='{a}']")
        ).to_have_count(1, timeout=SLOW)
        moved = await ok(client, "get_node", node_id=a)
        assert (moved["parent_id"], moved["version"]) == (s2, version + 1)
        event = (await ok(client, "node_history", node_id=a))["items"][-1]
        assert (event["action"], event["actor"]) == ("move", "Ada Lovelace")
        assert event["before"] == {"parent_id": s1, "version": version}
        assert event["after"] == {"parent_id": s2, "version": version + 1}
        # The counts follow: the story it left and the story it joined, and the epic's total.
        assert await counts(s1) == "0 of 2 tasks done" and await counts(s2) == "1 of 2 tasks done"
        drawn = await tree_of(page, epic)
        assert [child["progress"] for child in drawn["children"]] == [
            await counts(s1),
            await counts(s2),
        ]
        assert drawn["progress"] == await counts(epic)
        assert [t["id"] for t in drawn["children"][1]["children"]] == [a, d]

        # A drop that makes no sense is refused in the words move_node gives, and nothing moves.
        said = await refused(
            client, "move_node", actor="x", node_id=s1, expected_version=1, new_parent_id=s2
        )
        await page.drag_and_drop(
            f"li.node[data-id='{s1}'] > .row", f"li.node[data-id='{s2}'] > .row"
        )
        refusal = page.locator(".notice.refusal .notice-text").first
        await expect(refusal).to_be_visible(timeout=SLOW)
        plain(await refusal.inner_text(), said)
        assert (await ok(client, "get_node", node_id=s1))["parent_id"] == epic

        # The Move dialog and the keyboard: M on a task, choose the first story, move.
        await page.locator(f"li.node[data-id='{b}']").focus()
        await page.keyboard.press("m")
        dialog = page.locator("dialog.modal")
        await expect(dialog).to_be_visible()
        await dialog.locator("#move-to").select_option(str(s2))
        await dialog.get_by_role("button", name="Move").click()
        await expect(
            page.locator(f"li.node[data-id='{s2}'] > ul > li.node[data-id='{b}']")
        ).to_have_count(1, timeout=SLOW)
        assert (await ok(client, "get_node", node_id=b))["parent_id"] == s2
        assert w.problems == [], w.problems


async def test_the_outline_opens_and_closes_branches_and_is_walked_with_the_arrow_keys(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Walk {word}")
    story = await made(client, "story", f"Walked story {word}", epic)
    task = await made(client, "task", f"Walked task {word}", story)
    async with browser_page(board, token=the_token()) as w:
        page = w.page
        await w.open("#/outline")
        top = page.locator(f"li.node[data-id='{epic}']")
        await expect(top).to_be_visible(timeout=SLOW)
        assert await top.get_attribute("aria-expanded") == "true"
        assert await top.get_attribute("aria-level") == "1"
        await top.focus()
        await page.keyboard.press("ArrowDown")  # to the story
        assert await page.evaluate("document.activeElement.dataset.id") == str(story)
        await page.keyboard.press("ArrowRight")  # a branch that is open: into it
        assert await page.evaluate("document.activeElement.dataset.id") == str(task)
        await page.keyboard.press("ArrowLeft")  # a task has no branch: up to its story
        assert await page.evaluate("document.activeElement.dataset.id") == str(story)
        await page.keyboard.press("ArrowLeft")  # the story's branch closes
        await expect(page.locator(f"li.node[data-id='{story}']")).to_have_attribute(
            "aria-expanded", "false"
        )
        assert f"closed={story}" in page.url
        await expect(page.locator(f"li.node[data-id='{task}']")).to_be_hidden()
        await page.keyboard.press("ArrowRight")  # and opens again
        await expect(page.locator(f"li.node[data-id='{task}']")).to_be_visible()
        assert "closed=" not in page.url
        await page.keyboard.press("Enter")
        await expect(page.locator("#card-title")).to_contain_text(f"#{story} Walked story")
        # Close all, and open all.
        await page.locator("#collapse-all").click()
        await expect(page.locator(f"li.node[data-id='{epic}']")).to_have_attribute(
            "aria-expanded", "false"
        )
        await page.locator("#expand-all").click()
        await expect(page.locator(f"li.node[data-id='{task}']")).to_be_visible()
        assert w.problems == [], w.problems


# -- 6. a view is its address, and the server keeps nothing for it -------------------------------


def database_footprint() -> tuple[str, str]:
    """Every table of the database, and how many rows each holds that a view could add to."""
    tables = run_sql(
        "SELECT string_agg(table_name, ',' ORDER BY table_name) FROM information_schema.tables "
        "WHERE table_schema = 'public'",
        plain=True,
    ).stdout.strip()
    rows = run_sql(
        "SELECT (SELECT count(*) FROM events) || ',' || (SELECT count(*) FROM nodes) || ',' || "
        "(SELECT count(*) FROM memories) || ',' || (SELECT count(*) FROM claims)",
        plain=True,
    ).stdout.strip()
    return tables, rows


async def test_a_views_address_opens_the_same_view_in_a_second_browser_and_stores_nothing(
    client: Client, board: str
) -> None:
    ids = await twelve(client)
    epic = ids["epic"]
    async with browser_page(board, token=the_token()) as first:
        page = first.page
        await first.open(f"#/table?q=epic:{epic}")
        await shown_ids(page, 12)
        await apply_filter(page, f"epic:{epic} kind:task -claimed priority:high,urgent")
        wanted = await all_nodes(
            client, query=f"epic:{epic} kind:task -claimed priority:high,urgent"
        )
        await shown_ids(page, len(wanted))
        await sort_by(page, "priority", "ascending")
        await sort_by(page, "priority", "descending")
        await page.locator("#group").select_option("status")
        await expect(page.locator("th[data-column=priority]")).to_have_attribute(
            "aria-sort", "descending"
        )
        await expect(page.locator("tbody.group").first).to_be_visible()
        address = page.url
        first_view = (await groups(page), await page.locator("#filter").input_value())
        assert (
            "sort=-priority" in address and "group=status" in address and the_token() not in address
        )

        before = database_footprint()
        # A second browser, which has never seen this view: it signs in and opens the address.
        async with browser_page(board, token=the_token(), name="Someone else") as second:
            await second.page.goto(address)
            await expect(second.page.locator("#count")).to_contain_text(
                f"{len(wanted)} item", timeout=SLOW
            )
            assert await second.page.locator("#filter").input_value() == first_view[1]
            assert await groups(second.page) == first_view[0]
            assert await second.page.locator("#group").input_value() == "status"
            await expect(second.page.locator("th[data-column=priority]")).to_have_attribute(
                "aria-sort", "descending"
            )
            assert second.problems == [], second.problems
        assert database_footprint() == before
        tables = before[0].split(",")
        assert not [
            name for name in tables if "view" in name or "saved" in name or "filter" in name
        ]


async def test_the_outline_and_roadmap_keep_their_choices_in_the_address_too(
    client: Client, board: str
) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Kept {word}")
    story = await made(client, "story", f"Kept story {word}", epic)
    await made(client, "task", f"Kept task {word}", story, start_on=day(1), target_on=day(5))
    async with browser_page(board, token=the_token()) as first:
        await first.open(f"#/outline?closed={story}")
        await expect(first.page.locator(f"li.node[data-id='{story}']")).to_have_attribute(
            "aria-expanded", "false", timeout=SLOW
        )
        await first.open(f"#/roadmap?q=epic:{epic}&zoom=quarter&group=status")
        await expect(first.page.locator(".rm-track")).to_have_attribute(
            "data-zoom", "quarter", timeout=SLOW
        )
        roadmap_address = first.page.url
    async with browser_page(board, token=the_token()) as second:
        await second.page.goto(roadmap_address)
        await expect(second.page.locator(".rm-track")).to_have_attribute(
            "data-zoom", "quarter", timeout=SLOW
        )
        assert await second.page.locator("#filter").input_value() == f"epic:{epic}"
        assert await second.page.locator("#group").input_value() == "status"
        assert await second.page.locator(".rm-group").count() >= 1
