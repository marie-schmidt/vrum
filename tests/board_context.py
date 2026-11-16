"""What the browser tests of the Graph, its context list, requirements and references share (M16):
finding the same record in the list, on the map and in the detail panel, and reading what `recall`
says as the panel draws it. The page helpers of every browser test are in board_helpers.py."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp import Client
from playwright.async_api import Locator, Page, expect

from tests.board_helpers import SLOW, Watched, board_url, made
from tests.helpers import confirm, connect, make_decision, make_fact, ok
from tests.world import Project, make_project, register

ADA = "Ada Lovelace"

# The sections of a recall, in the order the server gives them.
RECALL_SECTIONS = [
    "active_decisions",
    "project_wide_decisions",
    "facts",
    "past_decisions",
    "past_facts",
]

# What no part of the board may name any more: the old taxonomy of beliefs, principles and memories.
REMOVED_WORDS = re.compile(r"\b(belief|beliefs|principle|principles|memory|memories)\b", re.I)


def dialog(page: Page) -> Locator:
    return page.locator("dialog.modal")


def refusal_in(page: Page) -> Locator:
    """The refusal a dialog shows, under its own fields."""
    return page.locator("dialog.modal form > .problem")


def svg(page: Page) -> Locator:
    return page.locator("svg#graph-svg")


def dot(page: Page, key: str) -> Locator:
    return svg(page).locator(f".dot[data-key='{key}']")


def edge(page: Page, kind: str, source: str, target: str) -> Locator:
    return svg(page).locator(
        f".edge[data-type='{kind}'][data-source='{source}'][data-target='{target}']"
    )


def listed(page: Page, key: str) -> Locator:
    """The row of the Graph's list (work tree or context list) that holds this record."""
    return page.locator(f"#graph-list [data-key='{key}']")


def panel(page: Page) -> Locator:
    return page.locator("#panel")


async def graph_open(w: Watched, address: str) -> None:
    """Open a Graph address and wait until what it asks for is drawn (the dots, or the list)."""
    await w.open(address)
    await expect(w.page.locator("#graph-page")).to_be_visible(timeout=SLOW)
    if "as=list" in address:
        await expect(w.page.locator("#graph-list")).to_be_visible(timeout=SLOW)
    else:
        await expect(svg(w.page)).to_have_attribute("data-ready", "true", timeout=SLOW)


async def show(page: Page, view: str | None = None, presentation: str | None = None) -> None:
    """Choose a preset and a presentation with the buttons, and wait until the page says so."""
    if view:
        await page.locator(f"#graph-view-{view}").click()
        await expect(page.locator("#graph-page")).to_have_attribute("data-view", view, timeout=SLOW)
    if presentation:
        await page.locator(f"#graph-as-{presentation}").click()
        await expect(page.locator("#graph-page")).to_have_attribute(
            "data-as", presentation, timeout=SLOW
        )
        if presentation == "map":
            await expect(svg(page)).to_have_attribute("data-ready", "true", timeout=SLOW)
        else:
            await expect(page.locator("#graph-list")).to_be_visible(timeout=SLOW)


async def hash_params(page: Page) -> dict[str, str]:
    """The address after the #, as the choices it holds."""
    found: dict[str, str] = await page.evaluate(
        "Object.fromEntries(new URLSearchParams(location.hash.split('?')[1] || ''))"
    )
    return found


async def dot_keys(page: Page) -> list[str]:
    keys: list[str] = (
        await svg(page).locator(".dot").evaluate_all("els => els.map(e => e.dataset.key)")
    )
    return sorted(keys)


async def map_edges(page: Page) -> set[tuple[str, str, str]]:
    """Every line the map draws, as (kind, source, target)."""
    rows: list[list[str]] = (
        await svg(page)
        .locator(".edge")
        .evaluate_all("els => els.map(e => [e.dataset.type, e.dataset.source, e.dataset.target])")
    )
    return {(kind, source, target) for kind, source, target in rows}


async def drawn_as(page: Page, dots: set[str], edges: set[tuple[str, str, str]]) -> None:
    """Wait until the map draws exactly these dots and lines (the page draws a moment after the
    address changes), and say what is different when it does not."""
    got: tuple[list[str], set[tuple[str, str, str]]] = ([], set())
    for _ in range(SLOW // 250):
        got = (await dot_keys(page), await map_edges(page))
        if got == (sorted(dots), edges):
            return
        await anyio.sleep(0.25)
    assert got[0] == sorted(dots), (set(got[0]) - dots, dots - set(got[0]))
    assert got[1] == edges, (got[1] - edges, edges - got[1])


async def listed_keys(page: Page) -> list[str]:
    """The records the list shows: tree rows of work, and rows of requirements, decisions, facts.
    A section that is hidden (Work in the Context preset) shows nothing."""
    keys: list[str] = await page.locator(
        "#work-tree li[role=treeitem][data-key], #graph-list .gv-record[data-key]"
    ).evaluate_all("els => els.filter(e => e.getClientRects().length).map(e => e.dataset.key)")
    return sorted(keys)


async def list_as(page: Page, rows: set[str]) -> None:
    """Wait until the list holds exactly these records (it draws a moment after the choice)."""
    got: list[str] = []
    for _ in range(SLOW // 250):
        got = await listed_keys(page)
        if got == sorted(rows):
            return
        await anyio.sleep(0.25)
    assert got == sorted(rows), (set(got) - rows, rows - set(got))


async def listed_connections(page: Page) -> set[tuple[str, str, str]]:
    """What the rows of the list say each record is joined to, as (kind, source, target), the way
    the map's lines read: each row names the line and the record at its other end."""
    rows: list[list[Any]] = await page.locator("#graph-list details.gv-connections").evaluate_all(
        """els => els.filter(d => d.getClientRects().length).flatMap(
            d => [...d.querySelectorAll('li[data-other]')].map(
                li => [d.dataset.for, li.dataset.other, li.dataset.relation, li.textContent]))"""
    )
    return {(relation, mine, other) for mine, other, relation, _ in rows}


def tied(connections: set[tuple[str, str, str]], mine: str, other: str, kind: str) -> bool:
    return (kind, mine, other) in connections or (kind, other, mine) in connections


async def tally(client: Client) -> dict[str, int]:
    """How much context and how many requirements there are, to see that a refusal wrote nothing."""
    return {
        "facts": (await ok(client, "list_facts", limit=1))["total"],
        "decisions": (await ok(client, "list_decisions", limit=1))["total"],
        "rules": (await ok(client, "list_rules", limit=1))["total"],
    }


async def whole(client: Client, tool: str, **arguments: Any) -> list[dict[str, Any]]:
    """Every item of a list tool, following its pages."""
    found: list[dict[str, Any]] = []
    after = None
    while True:
        page = await ok(client, tool, limit=200, after_id=after, **arguments)
        found.extend(page["items"])
        after = page["next_after_id"]
        if after is None:
            return found


def expected_recall(
    recalled: dict[str, Any],
) -> dict[str, tuple[int, list[tuple[str, int, str, str, str]]]]:
    """What a recall says, as the detail panel draws it: for each section that holds something, its
    count and each entry as (kind, id, lifecycle, confirmation, why). An empty section is left
    out."""
    found: dict[str, tuple[int, list[tuple[str, int, str, str, str]]]] = {}
    for name in RECALL_SECTIONS:
        entries = recalled[name]
        if not entries["items"]:
            continue
        rows = []
        for item in entries["items"]:
            kind = "decision" if "decision" in item else "fact"
            record = item[kind]
            rows.append((kind, record["id"], record["status"], record["confirmation"], item["why"]))
        found[name] = (entries["count"], rows)
    return found


async def recall_in_panel(
    page: Page,
) -> dict[str, tuple[int, list[tuple[str, int, str, str, str]]]]:
    """The recall sections the detail panel shows, in the same shape as expected_recall."""
    sections: list[dict[str, Any]] = (
        await panel(page)
        .locator(".recall-section")
        .evaluate_all(
            """els => els.map(s => ({
            name: s.dataset.section,
            count: Number(s.dataset.count),
            rows: [...s.querySelectorAll('li.record')].map(li => [
                li.dataset.kind, Number(li.dataset.record.split(':')[1]), li.dataset.status,
                li.dataset.confirmation, (li.querySelector('.why-text') || {}).textContent || '',
            ]),
        }))"""
        )
    )
    return {
        s["name"]: (s["count"], [(k, i, st, c, why) for k, i, st, c, why in s["rows"]])
        for s in sections
        if s["name"] != "similar"
    }


async def recall_matches(client: Client, page: Page, **target: int) -> None:
    """The panel's recall is what `recall` returns for the same item, section by section."""
    want = expected_recall(await ok(client, "recall", limit=50, **target))
    shown = await recall_in_panel(page)
    for _ in range(SLOW // 250):
        if shown == want:
            break
        await anyio.sleep(0.25)
        shown = await recall_in_panel(page)
    assert shown == want


async def history_actions(client: Client, tool: str, key: str, ident: int) -> list[str]:
    items = (await ok(client, tool, limit=200, **{key: ident}))["items"]
    return [event["action"] for event in items]


async def lifecycle_filter(page: Page, plural: str, status: str) -> None:
    """Choose which lifecycle a context section lists: active, superseded, withdrawn or ''."""
    await page.locator(f"#ctx-{plural}-status").select_option(status)


@asynccontextmanager
async def fresh_project(server_url: str) -> AsyncIterator[tuple[Client, str, Project]]:
    """A project of its own, with nothing in it (an account, a project and an agent key made as a
    person would), for a test that compares whole lists such as the coverage report. Gives a client
    of that project, the address of its board and the project."""
    project = make_project(register())
    async with connect(server_url, project.key) as client:
        yield client, board_url(server_url, project), project


async def coverage_numbers(client: Client) -> tuple[int, int]:
    """What the coverage report says: requirements in no story, and stories with no requirement."""
    report = await ok(client, "coverage_report", show="both", limit=1)
    return int(report["unplaced_rule_count"]), int(report["story_without_rules_count"])


async def context_world(client: Client, word: str) -> dict[str, int]:
    """Work and context of every sort, made through MCP in an empty project, to look at on the
    Graph. Two epics (E, with two stories and three tasks, and E2 with one story and one task), two
    links between work, requirements (held, settled, one in no story and one dropped), decisions
    (confirmed, project-wide, replaced, withdrawn, and one about E2) and facts (confirmed, about a
    decision, about nothing, replaced, withdrawn, and one about E2). Gives the ids by short name."""
    ids: dict[str, int] = {}

    async def node(name: str, kind: str, parent: str | None = None) -> None:
        ids[name] = await made(client, kind, f"{name} {word}", ids[parent] if parent else None)

    for name, kind, parent in (
        ("E", "epic", None),
        ("s1", "story", "E"),
        ("s2", "story", "E"),
        ("t1", "task", "s1"),
        ("t2", "task", "s1"),
        ("t3", "task", "s2"),
        ("E2", "epic", None),
        ("s9", "story", "E2"),
        ("t9", "task", "s9"),
    ):
        await node(name, kind, parent)
    for source, target, kind in (("t2", "t1", "needs_first"), ("s1", "s2", "relates_to")):
        await ok(
            client,
            "add_link",
            actor="a",
            source_id=ids[source],
            target_id=ids[target],
            link_type=kind,
        )

    async def rule(name: str, story: str | None, **more: Any) -> None:
        arguments = {"story_id": ids[story]} if story else {}
        made_rule = await ok(
            client, "create_rule", actor="a", title=f"{name} {word}", **arguments, **more
        )
        ids[name] = made_rule["id"]

    await rule("r1", "s1")
    await rule("r2", "s2", status="settled")
    await rule("ru", None)
    await rule("rd", "s2", status="dropped")

    async def decision(name: str, **more: Any) -> dict[str, Any]:
        record = await make_decision(client, f"{name} {word}", **more)
        ids[name] = record["id"]
        return record

    await decision("d1", node_ids=[ids["s1"]], rule_ids=[ids["r1"]])
    await decision("dw", project_wide=True)
    old = await decision("d_old", node_ids=[ids["s2"]])
    newer = await ok(
        client,
        "supersede_decision",
        actor="a",
        decision_id=old["id"],
        title=f"d_new {word}",
        text="Changed.",
        reason="Learned more.",
        decided_by="Marie",
        keep_links=True,
    )
    ids["d_new"] = newer["id"]
    gone = await decision("d_gone", node_ids=[ids["s1"]])
    await ok(client, "withdraw_decision", actor="a", decision_id=gone["id"], reason="Not needed")
    await decision("d9", node_ids=[ids["s9"]])

    async def fact(name: str, **more: Any) -> dict[str, Any]:
        record = await make_fact(client, f"{name} {word}", **more)
        ids[name] = record["id"]
        return record

    await fact("f1", node_ids=[ids["s1"]], rule_ids=[ids["r1"]])
    await fact("f2", decision_ids=[ids["d1"]])
    await fact("f3")
    old_fact = await fact("f_old", node_ids=[ids["s2"]])
    replacement = await ok(
        client,
        "supersede_fact",
        actor="a",
        replaces=[old_fact["id"]],
        reason="Measured again",
        text=f"f_new {word}",
        source="A measurement",
        keep_links=True,
    )
    ids["f_new"] = replacement["replacement"]["id"]
    gone_fact = await fact("f_gone", node_ids=[ids["s1"]])
    await ok(client, "withdraw_fact", actor="a", fact_ids=[gone_fact["id"]], reason="A slip")
    await fact("f9", node_ids=[ids["s9"]])
    await confirm(client, "decision", {"id": ids["d1"]}, "Marie")
    await confirm(client, "fact", {"id": ids["f1"]}, "Marie")
    return ids


async def server_edges(client: Client) -> set[tuple[str, str, str]]:
    """Every line the Graph could draw between the records of a project, worked out from what the
    tools say (nodes, links, requirements, decisions and facts), as (kind, source, target) with
    the keys the board uses: node:12, rule:3, decision:7, fact:5."""
    found: set[tuple[str, str, str]] = set()
    nodes = await whole(client, "list_nodes")
    for node in nodes:
        if node["parent_id"] is not None:
            found.add(("hierarchy", f"node:{node['parent_id']}", f"node:{node['id']}"))
        for link in await whole(client, "list_links", node_id=node["id"]):
            found.add((link["link_type"], f"node:{link['source_id']}", f"node:{link['target_id']}"))
    for rule in await whole(client, "list_rules"):
        if rule["story_id"] is not None:
            found.add(("placed", f"rule:{rule['id']}", f"node:{rule['story_id']}"))
    for kind, tool in (("decision", "list_decisions"), ("fact", "list_facts")):
        for record in await whole(client, tool):
            me = f"{kind}:{record['id']}"
            for ref, key in (
                ("node", "node_ids"),
                ("rule", "rule_ids"),
                ("decision", "decision_ids"),
            ):
                for other in record.get(key, []):
                    found.add(("concerns", me, f"{ref}:{other}"))
            if record["superseded_by_id"] is not None:
                found.add(("replaces", f"{kind}:{record['superseded_by_id']}", me))
    return found


async def check_same_relationships(page: Page, list_rows: set[str]) -> None:
    """The map and the list, drawn for the same choices, hold the same lines: every line of the
    map is said by the rows of the list at both its ends (a row that exists), and every line a
    row says between two records the map draws is a line of the map. `list_rows` is the records
    the list is to hold. Leaves the page on the map again."""
    keys = set(await dot_keys(page))
    drawn = await map_edges(page)
    assert drawn, "the map draws no lines"
    await page.locator("#graph-as-list").click()
    await expect(page.locator("#graph-page")).to_have_attribute("data-as", "list", timeout=SLOW)
    await list_as(page, list_rows)
    rows = set(await listed_keys(page))
    said = await listed_connections(page)
    for kind, source, target in drawn:
        if kind == "hierarchy":
            nested = await page.locator(f"#work-tree li[data-key='{target}']").evaluate(
                "(e, parent) => e.parentElement.closest('li')?.dataset.key === parent", source
            )
            assert nested, (kind, source, target)
        elif kind == "needs_first":
            ties = page.locator(f"#work-tree li[data-key='{source}'] .gv-ties.needs").first
            assert target.split(":")[1] in (await ties.get_attribute("data-ids") or "").split(",")
        else:
            seen = [
                ((kind, source, target) in said) if source in rows else None,
                ((kind, target, source) in said) if target in rows else None,
            ]
            assert any(s is not None for s in seen) and all(s is not False for s in seen), (
                kind,
                source,
                target,
            )
    for kind, mine, other in said:
        if mine in keys and other in keys:
            assert (kind, mine, other) in drawn or (kind, other, mine) in drawn, (kind, mine, other)
    await page.locator("#graph-as-map").click()
    await expect(page.locator("#graph-page")).to_have_attribute("data-as", "map", timeout=SLOW)
    await expect(svg(page)).to_have_attribute("data-ready", "true", timeout=SLOW)
