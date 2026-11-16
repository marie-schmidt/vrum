"""Chapter 10 of the acceptance scenario: the views of M11 (Table, Outline, Roadmap and the one
filter language), driven in a real headless browser against the real containers, with what
happens read back through a real MCP client. Each proof is shown the way a person would see it:
what was done in the browser, and what the graph then holds."""

import re
from typing import Any

from mcp import Client
from playwright.async_api import Error as BrowserError
from playwright.async_api import expect

from tests.acceptance_scenario import URL, Failed, chapter, clip, proof
from tests.board_helpers import (
    SLOW,
    apply_filter,
    bar_of,
    board_url,
    browser_page,
    groups,
    hold_back_news,
    on,
    roadmap_ids,
    shown_ids,
    sort_by,
    timeline,
)
from tests.board_helpers import drag_bar as drag
from tests.helpers import all_nodes, connect, day, ok, refused, run_sql, the_token, unique_word

ADA = "Ada Lovelace"


async def made(
    client: Client, kind: str, title: str, parent: int | None = None, **more: Any
) -> int:
    fields: dict[str, Any] = {"actor": "agent-1", "kind": kind, "title": title, **more}
    if parent:
        fields["parent_id"] = parent
    return int((await ok(client, "create_node", **fields))["id"])


def same_words(board_words: str, mcp_words: str) -> bool:
    """The board said what the tool says, without the protocol's "Error executing tool"."""
    return bool(board_words.strip()) and mcp_words.endswith(board_words.strip())


async def updates(client: Client, node_id: int) -> list[dict[str, Any]]:
    history = (await ok(client, "node_history", node_id=node_id, limit=200))["items"]
    return [event for event in history if event["action"] == "update"]


async def dates(client: Client, node_id: int) -> tuple[str | None, str | None]:
    node = await ok(client, "get_node", node_id=node_id)
    return node["start_on"], node["target_on"]


async def views() -> None:
    chapter("The views: Table, Outline and Roadmap, and one filter language")
    page_url = board_url(URL)
    try:
        async with connect(URL, the_token()) as client:
            await the_table(client, page_url)
            await changing_rows(client, page_url)
            await the_outline(client, page_url)
            await the_roadmap(client, page_url)
            await dragging_bars(client, page_url)
            await keys_and_tray(client, page_url)
            await addresses(client, page_url)
    except BrowserError as error:
        raise Failed(
            f"M11: the board did not do what a proof needs: {clip(str(error), 220)}"
        ) from error


# -- 1 --------------------------------------------------------------------------------------------


async def a_dozen(client: Client) -> dict[str, int]:
    """One epic, three stories and eight tasks that differ in status, priority, who holds them,
    dates and what they wait for: twelve items."""
    word = unique_word()
    ids = {"epic": await made(client, "epic", f"Twelve {word}")}
    for name, priority in (("s1", "high"), ("s2", "normal"), ("s3", "urgent")):
        ids[name] = await made(client, "story", f"{name} {word}", ids["epic"], priority=priority)
    for name, story, priority, more in (
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
            client, "task", f"Task {name} {word}", ids[story], priority=priority, **more
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


async def the_table(client: Client, page_url: str) -> None:
    ids = await a_dozen(client)
    epic = ids["epic"]
    everything = await all_nodes(client, query=f"epic:{epic}")
    text = (
        f"epic:{epic} kind:task status:ready,in_progress,blocked -claimed priority:high,urgent "
        f"OR epic:{epic} kind:story"
    )
    wanted = await all_nodes(client, query=text)
    async with browser_page(page_url, token=the_token()) as w:
        page = w.page
        await w.open(f"#/table?q=epic:{epic}")
        every = sorted(await shown_ids(page, 12))
        await apply_filter(page, text)
        filtered = sorted(await shown_ids(page, len(wanted)))
        await apply_filter(page, f"epic:{epic}")
        await shown_ids(page, 12)
        await sort_by(page, "priority", "ascending")
        await sort_by(page, "priority", "descending")
        by_priority = await shown_ids(page, 12)
        await w.go(f"#/table?q=epic:{epic}&group=status")
        by_status = await groups(page)
        bad = f"epic:{epic} colour:red"
        said = await refused(client, "list_nodes", query=bad)
        await w.go(f"#/table?q=epic:{epic}")
        await shown_ids(page, 12)
        await apply_filter(page, bad)
        problem = page.locator("#filter-problem")
        await expect(problem).to_contain_text("there is no field called 'colour'")
        shown_problem = await problem.inner_text()
        rows_kept = await page.locator("tr.item").count()
    order = {"urgent": 3, "high": 2, "normal": 1, "low": 0}
    proof(
        "M11",
        "the Table shows twelve items, and the filter, the sort and the groups give the rows "
        "list_nodes gives for the same text",
        every == sorted(n["id"] for n in everything)
        and filtered == sorted(n["id"] for n in wanted)
        and 0 < len(wanted) < 12
        and by_priority
        == [n["id"] for n in sorted(everything, key=lambda n: (-order[n["priority"]], n["id"]))]
        and all(
            by_status[status] == [n["id"] for n in everything if n["status"] == status]
            for status in {n["status"] for n in everything}
        ),
        f"{len(wanted)} of 12 match the filter",
    )
    proof(
        "M11",
        "the filter language takes words, field:value, commas, OR, a leading minus and no:field, "
        "and the same text through MCP list_nodes gives the same ids",
        sorted(n["id"] for n in wanted) == filtered
        and {n["id"] for n in await all_nodes(client, query=f"epic:{epic} no:claimed kind:task")}
        == {n["id"] for n in everything if n["kind"] == "task" and not n["claimed_by"]}
        and {n["id"] for n in await all_nodes(client, query=f"epic:{epic} is:overdue")}
        == {ids["t6"]},
    )
    proof(
        "M11",
        "a query that cannot be read is refused in plain words, the same in the Table and through "
        "MCP, and the rows stay",
        same_words(shown_problem, said) and rows_kept == 12,
        clip(shown_problem, 90),
    )


# -- 2 --------------------------------------------------------------------------------------------


async def changing_rows(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Bulk {word}")
    story = await made(client, "story", f"Story {word}", epic)
    t1, t2, t3, t4 = [await made(client, "task", f"Task {n} {word}", story) for n in range(4)]
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/table?q=epic:{epic} kind:task")
        await shown_ids(page, 4)
        for node_id in (t1, t2, t3):
            await page.locator(f"tr.item[data-id='{node_id}'] input[type=checkbox]").check()
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
        refusal = await result.locator(f"li[data-id='{t2}'] .refusal-text").inner_text()
    changed = [await ok(client, "get_node", node_id=n) for n in (t1, t3)]
    held = await ok(client, "get_node", node_id=t2)
    histories = [await updates(client, n) for n in (t1, t3)]
    proof(
        "M11",
        "three rows chosen and changed at once: the row an agent changed meanwhile is refused with "
        "the stale-version message, the other two change, and the history shows the person",
        all(n["priority"] == "urgent" and n["status"] == "in_progress" for n in changed)
        and (held["priority"], held["status"]) == ("normal", "ready")
        and same_words(re.sub(r"^not changed\. ", "", refusal), said)
        and all(
            len(h) == 1 and h[0]["actor"] == ADA and h[0]["after"]["version"] == 2
            for h in histories
        )
        and len(await updates(client, t2)) == 1,
        clip(refusal, 90),
    )


# -- 3 --------------------------------------------------------------------------------------------


async def the_outline(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Outline {word}")
    s1 = await made(client, "story", f"First {word}", epic)
    s2 = await made(client, "story", f"Second {word}", epic)
    a, _, _ = [await made(client, "task", f"Task {n} {word}", s1) for n in "abc"]
    await made(client, "task", f"Task d {word}", s2)
    await ok(client, "update_node", actor="x", node_id=a, expected_version=1, status="done")

    async def counts(node_id: int) -> str:
        node = await ok(client, "get_node", node_id=node_id)
        return f"{node['progress_done']} of {node['progress_total']} task" + (
            "" if node["progress_total"] == 1 else "s"
        )

    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open("#/outline")
        branch = page.locator(f"li.node[data-id='{epic}']")
        await expect(branch).to_be_visible(timeout=SLOW)
        drawn: list[int] = await branch.locator("li.node").evaluate_all(
            "els => els.map(e => Number(e.dataset.id))"
        )
        expected = [
            n["id"] for n in await all_nodes(client, query=f"epic:{epic}") if n["id"] != epic
        ]
        same_tree = sorted(drawn) == sorted(expected)
        before = [await counts(s1), await counts(s2)]
        words_before = await page.locator(f"li.node[data-id='{s1}'] > .row").inner_text()
        version = (await ok(client, "get_node", node_id=a))["version"]
        await page.drag_and_drop(
            f"li.node[data-id='{a}'] > .row", f"li.node[data-id='{s2}'] > .row"
        )
        await expect(
            page.locator(f"li.node[data-id='{s2}'] > ul > li.node[data-id='{a}']")
        ).to_have_count(1, timeout=SLOW)
        words_after = await page.locator(f"li.node[data-id='{s1}'] > .row").inner_text()
    moved = await ok(client, "get_node", node_id=a)
    event = (await ok(client, "node_history", node_id=a, limit=200))["items"][-1]
    proof(
        "M11",
        "the Outline shows the tree list_nodes gives; dragging a task to another story matches get "
        "and the history, and the counts of the branches follow",
        same_tree
        and (moved["parent_id"], moved["version"]) == (s2, version + 1)
        and (event["action"], event["actor"]) == ("move", ADA)
        and event["before"] == {"parent_id": s1, "version": version}
        and before == ["1 of 3 tasks", "0 of 1 task"]
        and [await counts(s1), await counts(s2)] == ["0 of 2 tasks", "1 of 2 tasks"]
        and "1 of 3" in words_before
        and "0 of 2" in words_after,
        f"{before} -> {[await counts(s1), await counts(s2)]}",
    )


# -- 4 --------------------------------------------------------------------------------------------


async def the_roadmap(client: Client, page_url: str) -> None:
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
        client,
        "add_link",
        actor="a",
        source_id=ids["b"],
        target_id=ids["a"],
        link_type="needs_first",
    )
    async with browser_page(page_url, token=the_token()) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{epic}")
        everyone = await roadmap_ids(page, 8, 2)
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
        tray: list[int] = await page.locator(".tray-item").evaluate_all(
            "els => els.map(e => Number(e.dataset.id))"
        )
        arrow = page.locator("path.dep")
        arrows = [
            (
                await arrow.nth(i).get_attribute("data-from"),
                await arrow.nth(i).get_attribute("data-to"),
            )
            for i in range(await arrow.count())
        ]
        conflicts: list[int] = await page.locator(".rm-label:has(.chip.conflict)").evaluate_all(
            "els => els.map(e => Number(e.dataset.id))"
        )
        overdue: list[int] = await page.locator(".rm-label:has(.chip.overdue)").evaluate_all(
            "els => els.map(e => Number(e.dataset.id))"
        )
        words = await page.locator(".rm-label .chip").all_inner_texts()
        same_filters = True
        for text, dated, loose in (
            (f"epic:{epic} target:{day(-30)}..{day(16)}", 4, 0),
            (f"epic:{epic} is:overdue", 1, 0),
            (f"epic:{epic} target:none kind:task", 0, 2),
        ):
            wanted = {n["id"] for n in await all_nodes(client, query=text)}
            await apply_filter(page, text)
            same_filters = same_filters and await roadmap_ids(page, dated, loose) == wanted
    proof(
        "M11",
        "the Roadmap draws each of six bars at the pixels its dates give, the epic and story as "
        "worked-out spans, and the two undated items in the tray",
        everyone == {epic, story, *ids.values(), *undated}
        and where_right
        and worked_out == [("true", day(-20), day(30))] * 2
        and tray == undated,
        f"{line['ppd']:g} pixels a day, today {line['today']}",
    )
    proof(
        "M11",
        "the needs_first link is an arrow from the prerequisite, the schedule conflict is on the "
        "item that starts too early and the overdue mark on the past item, both in words",
        arrows == [(str(ids["a"]), str(ids["b"]))]
        and conflicts == [ids["b"]]
        and overdue == [ids["c"]]
        and any("Schedule conflict" in text for text in words)
        and any("Overdue" in text for text in words),
        ", ".join(sorted(words)),
    )
    proof(
        "M11",
        "a date filter in the Roadmap (target:RANGE, is:overdue, target:none) gives the same ids "
        "as the same text through MCP",
        same_filters,
    )


# -- 5 --------------------------------------------------------------------------------------------


async def dragging_bars(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Dragged {word}")
    story = await made(client, "story", f"Part {word}", epic)
    moved = await made(client, "task", f"Moved {word}", story, start_on=day(3), target_on=day(12))
    ended = await made(client, "task", f"Ended {word}", story, start_on=day(4), target_on=day(14))
    stale = await made(client, "task", f"Stale {word}", story, start_on=day(5), target_on=day(15))
    wrong = await made(client, "task", f"Wrong {word}", story, start_on=day(6), target_on=day(16))
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{epic}")
        await roadmap_ids(page, 6, 0)
        await drag(page, moved, 5)
        await expect(bar_of(page, moved)).to_have_attribute("data-start", day(8), timeout=SLOW)
        await drag(page, ended, -3, "end")
        await expect(bar_of(page, ended)).to_have_attribute("data-end", day(11), timeout=SLOW)

        await hold_back_news(page)
        await ok(
            client,
            "update_node",
            actor="agent-1",
            node_id=stale,
            expected_version=1,
            start_on=day(1),
            target_on=day(2),
        )
        said_stale = await refused(
            client,
            "update_node",
            actor="x",
            node_id=stale,
            expected_version=1,
            start_on=day(10),
            target_on=day(20),
        )
        await drag(page, stale, 5)
        refusal = page.locator(".notice.refusal .notice-text", has_text="is at version 2")
        await expect(refusal).to_be_visible(timeout=SLOW)
        stale_words = await refusal.inner_text()
        await expect(bar_of(page, stale)).to_have_attribute("data-start", day(1), timeout=SLOW)

        said_wrong = await refused(
            client, "update_node", actor="x", node_id=wrong, expected_version=1, start_on=day(26)
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
        "dragging a Roadmap bar moves both dates and dragging an end moves one, each a versioned "
        "change that get_node and the history show with the person and the versions",
        await dates(client, moved) == (day(8), day(17))
        and (first["actor"], first["before"]["version"], first["after"]["version"]) == (ADA, 1, 2)
        and first["after"] == {"start_on": day(8), "target_on": day(17), "version": 2}
        and await dates(client, ended) == (day(4), day(11))
        and (second["actor"], second["after"]) == (ADA, {"target_on": day(11), "version": 2}),
        f"{day(3)}..{day(12)} -> {day(8)}..{day(17)}",
    )
    proof(
        "M11",
        "dragging a bar an agent changed meanwhile is refused with the stale-version message and "
        "the bar goes back; a start dragged past the target is refused with the MCP message",
        same_words(stale_words, said_stale)
        and same_words(wrong_words, said_wrong)
        and await dates(client, stale) == (day(1), day(2))
        and await dates(client, wrong) == (day(6), day(16))
        and [e["actor"] for e in await updates(client, stale)] == ["agent-1"]
        and await updates(client, wrong) == [],
        clip(wrong_words, 90),
    )


async def keys_and_tray(client: Client, page_url: str) -> None:
    word = unique_word()
    epic = await made(client, "epic", f"Keys {word}")
    story = await made(client, "story", f"Part {word}", epic)
    keyed = await made(client, "task", f"Keyed {word}", story, start_on=day(3), target_on=day(12))
    loose = await made(client, "task", f"Loose {word}", story)
    async with browser_page(page_url, token=the_token(), name=ADA) as w:
        page = w.page
        await w.open(f"#/roadmap?q=epic:{epic}")
        await roadmap_ids(page, 3, 1)
        await bar_of(page, keyed).focus()
        for _ in range(3):
            await page.keyboard.press("ArrowRight")
        await expect(bar_of(page, keyed)).to_have_attribute("data-start", day(6), timeout=SLOW)
        await bar_of(page, keyed).focus()
        await page.keyboard.press("Shift+ArrowLeft")
        await expect(bar_of(page, keyed)).to_have_attribute("data-start", day(-1), timeout=SLOW)
        line = await timeline(page)
        at = ((line["today"] - line["origin"]).days + 2) * line["ppd"] + line["ppd"] / 2
        await page.drag_and_drop(
            f".tray-item[data-id='{loose}'] .tray-drag",
            ".rm-rows",
            target_position={"x": at, "y": 60},
        )
        await expect(bar_of(page, loose)).to_be_visible(timeout=SLOW)
    history = await updates(client, keyed)
    (given,) = await updates(client, loose)
    proof(
        "M11",
        "keys shift a bar by a day or a week (three presses are one change), and an undated item "
        "dropped on the timeline gets both dates, each shown by get_node and the history",
        await dates(client, keyed) == (day(-1), day(8))
        and [(e["actor"], e["after"]["version"]) for e in history] == [(ADA, 2), (ADA, 3)]
        and await dates(client, loose) == (day(2), day(8))
        and (given["actor"], given["before"]["version"], given["after"]["version"]) == (ADA, 1, 2),
        f"{day(2)}..{day(8)}",
    )


# -- 6 --------------------------------------------------------------------------------------------


def footprint() -> tuple[str, str]:
    """Every table of the database, and the counts of the rows a view could have added to."""
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


async def addresses(client: Client, page_url: str) -> None:
    ids = await a_dozen(client)
    epic = ids["epic"]
    text = f"epic:{epic} kind:task -claimed priority:high,urgent"
    wanted = await all_nodes(client, query=text)
    async with browser_page(page_url, token=the_token()) as first:
        await first.open(f"#/table?q=epic:{epic}")
        await shown_ids(first.page, 12)
        await apply_filter(first.page, text)
        await shown_ids(first.page, len(wanted))
        await sort_by(first.page, "priority", "ascending")
        await sort_by(first.page, "priority", "descending")
        await first.page.locator("#group").select_option("status")
        await expect(first.page.locator("tbody.group").first).to_be_visible()
        address = first.page.url
        seen = (await groups(first.page), await first.page.locator("#filter").input_value())
        before = footprint()
        async with browser_page(page_url, token=the_token(), name="Someone else") as second:
            await second.page.goto(address)
            await expect(second.page.locator("#count")).to_contain_text(
                f"{len(wanted)} item", timeout=SLOW
            )
            await expect(second.page.locator("th[data-column=priority]")).to_have_attribute(
                "aria-sort", "descending"
            )
            same = (
                await groups(second.page),
                await second.page.locator("#filter").input_value(),
            ) == seen
        after = footprint()
    proof(
        "M11",
        "a view's address opened in a second browser, signed in, shows the same filter, sort and "
        "groups, and the server stored nothing for it",
        same
        and after == before
        and "sort=-priority" in address
        and "group=status" in address
        and the_token() not in address
        and not [t for t in before[0].split(",") if "view" in t or "saved" in t or "filter" in t],
        address.split("#", 1)[1][:90],
    )
