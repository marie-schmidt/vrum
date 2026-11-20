"""What the browser tests of notes, acceptance criteria, references and the Graph share: a project
of its
own with work and notes of every sort, finding the same note in a list and on its own page, and
reading what `recall` says as a record's page draws it. The page helpers of every browser test are
in board_helpers.py."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp import Client
from playwright.async_api import Locator, Page

from tests.board_helpers import SLOW, board_url, item_page, made
from tests.helpers import (
    NODE_PAGES,
    confirm_on_board,
    connect,
    make_decision,
    make_note,
    ok,
)
from tests.world import Project, make_project, register

ADA = "Ada Lovelace"

# The sections of a recall, in the order the server gives them.
RECALL_SECTIONS = ["notes", "project_wide_notes", "past_notes"]

# What no part of the board may name any more: the old taxonomy of beliefs, principles and memories.
REMOVED_WORDS = re.compile(r"\b(belief|beliefs|principle|principles|memory|memories)\b", re.I)


def dialog(page: Page) -> Locator:
    return page.locator("dialog.modal")


def refusal_in(page: Page) -> Locator:
    """The refusal a dialog shows, under its own fields."""
    return page.locator("dialog.modal form > .problem")


async def hash_params(page: Page) -> dict[str, str]:
    """The address after the #, as the choices it holds."""
    found: dict[str, str] = await page.evaluate(
        "Object.fromEntries(new URLSearchParams(location.hash.split('?')[1] || ''))"
    )
    return found


async def tally(client: Client) -> dict[str, int]:
    """How many notes and acceptance criteria there are, to see that a refusal wrote nothing."""
    return {
        "notes": (await ok(client, "list_notes", limit=1))["total"],
        "rules": (await ok(client, "list_rules", limit=1))["total"],
    }


async def whole(client: Client, tool: str, **arguments: Any) -> list[dict[str, Any]]:
    """Every item of a list tool, following its pages (a page of nodes is continued by `after`,
    a reference; every other list by `after_id`)."""
    start, more = ("after", "next_after") if tool in NODE_PAGES else ("after_id", "next_after_id")
    found: list[dict[str, Any]] = []
    after = None
    while True:
        page = await ok(client, tool, limit=200, **{start: after}, **arguments)
        found.extend(page["items"])
        after = page[more]
        if after is None:
            return found


# -- recall, as a record's page draws it -----------------------------------------------------------


def expected_recall(
    recalled: dict[str, Any],
) -> dict[str, tuple[int, list[tuple[str, int, str, str]]]]:
    """What a recall says, as a record's page draws it: for each section that holds something, its
    count and each entry as (kind, id, status, why). An empty section is left out."""
    found: dict[str, tuple[int, list[tuple[str, int, str, str]]]] = {}
    for name in RECALL_SECTIONS:
        entries = recalled[name]
        if not entries["items"]:
            continue
        rows = [
            ("note", item["note"]["id"], item["note"]["status"], item["why"])
            for item in entries["items"]
        ]
        found[name] = (entries["count"], rows)
    return found


async def recall_on_page(
    page: Page,
) -> dict[str, tuple[int, list[tuple[str, int, str, str]]]]:
    """The recall sections the page of an epic, story, task or acceptance criterion draws, in the
    same shape
    as expected_recall. The tables of the closed "Earlier" and "Project-wide" sections are read
    too: a closed section still holds its rows."""
    sections: list[dict[str, Any]] = (
        await item_page(page)
        .locator(".recall-section")
        .evaluate_all(
            """els => els.map(s => ({
            name: s.dataset.section,
            count: Number(s.dataset.count),
            rows: [...s.querySelectorAll('tr.record-line')].map(tr => [
                tr.dataset.kind, Number(tr.dataset.id), tr.dataset.status, tr.dataset.why || '',
            ]),
        }))"""
        )
    )
    return {
        s["name"]: (s["count"], [(k, i, st, why) for k, i, st, why in s["rows"]])
        for s in sections
        if s["name"] != "similar"
    }


async def recall_matches(client: Client, page: Page, **target: Any) -> None:
    """The recall a record's page draws is what `recall` returns for the same item, section by
    section. The item is `node=<reference>` or `rule_id=<id>`, and its page is on show."""
    want = expected_recall(await ok(client, "recall", limit=50, **target))
    shown = await recall_on_page(page)
    for _ in range(SLOW // 250):
        if shown == want:
            break
        await anyio.sleep(0.25)
        shown = await recall_on_page(page)
    assert shown == want


async def history_actions(client: Client, tool: str, key: str, ident: int | str) -> list[str]:
    """The actions in an item's history, in the order the tool gives them: `key` is the argument
    that names the item (`node` with a reference, `rule_id` or `note_id` with a number)."""
    items = (await ok(client, tool, limit=200, **{key: ident}))["items"]
    return [event["action"] for event in items]


def board_refusal(project: Project, tool: str, **arguments: Any) -> str:
    """The words the board's own call to a tool is refused in, as a person signed in to the
    project's board would be (a tool only a person may use, such as set_confirmation, cannot be
    asked of an agent key)."""
    reply = project.owner.call("POST", f"/board/api/projects/{project.id}/tool/{tool}", arguments)
    assert reply.status == 400, (reply.status, reply.body)
    return str(reply.body["message"])


async def choose_in_menu(page: Page, chip: Locator, value: str) -> None:
    """Open the menu of a chip (the status of a note, a criterion or a piece of work) and choose
    its row."""
    menu_name = await chip.get_attribute("data-menu")
    await chip.click()
    menu = page.locator(f"div.vmenu[data-menu='{menu_name}']")
    await menu.locator(f".vmenu-option[data-value='{value}']").click()


# -- projects of their own ------------------------------------------------------------------------


@asynccontextmanager
async def fresh_project(server_url: str) -> AsyncIterator[tuple[Client, str, Project]]:
    """A project of its own, with nothing in it (an account, a project and an agent key made as a
    person would), for a test that compares whole lists such as the coverage report. The person who
    owns it is called Ada Lovelace, which is who a change made on its board is recorded as, and the
    agent key is the harness's ("tests"). Gives a client of that project, the address of its board
    and the project."""
    project = make_project(register(name=ADA))
    async with connect(server_url, project.key) as client:
        yield client, board_url(server_url, project), project


async def coverage_numbers(client: Client) -> tuple[int, int]:
    """What the coverage report says: acceptance criteria placed nowhere, and stories with none."""
    report = await ok(client, "coverage_report", show="both", limit=1)
    return int(report["unplaced_rule_count"]), int(report["story_without_rules_count"])


async def context_world(
    client: Client, word: str, project: Project | None = None
) -> dict[str, Any]:
    """Work and notes of every sort, made through MCP in an empty project, to look at on the board.
    Two epics (E, with two stories and three tasks, and E2 with one story and one task), two links
    between work, acceptance criteria (open, met, one placed nowhere and one dropped), notes someone
    decided
    (one confirmed, a project-wide one, one replaced, one withdrawn, and one about E2) and notes
    nobody decided (one confirmed, one about a note someone decided, one about nothing, one
    replaced, one withdrawn, and one about E2). Gives them by short name: work by its reference (a
    string, such as story:12), acceptance criteria and notes by their id (a number). Confirmed on
    the
    board, as a person, in `project` (the shared one by default)."""
    ids: dict[str, Any] = {}

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
            source=ids[source],
            target=ids[target],
            link_type=kind,
        )

    async def rule(name: str, story: str | None, **more: Any) -> None:
        arguments = {"node": ids[story]} if story else {}
        made_rule = await ok(client, "create_rule", title=f"{name} {word}", **arguments, **more)
        ids[name] = made_rule["id"]

    await rule("r1", "s1")
    await rule("r2", "s2", status="met")
    await rule("ru", None)
    await rule("rd", "s2", status="dropped")

    async def decision(name: str, **more: Any) -> dict[str, Any]:
        record = await make_decision(client, f"{name} {word}", **more)
        ids[name] = record["id"]
        return record

    await decision("d1", nodes=[ids["s1"]], rule_ids=[ids["r1"]])
    await decision("dw", project_wide=True)
    old = await decision("d_old", nodes=[ids["s2"]])
    newer = await ok(
        client,
        "supersede_note",
        replaces=[old["id"]],
        text=f"d_new {word}\n\nChanged.",
        why="Learned more.",
        decided_by="Marie",
        keep_links=True,
    )
    ids["d_new"] = newer["replacement"]["id"]
    gone = await decision("d_gone", nodes=[ids["s1"]])
    await ok(client, "withdraw_note", note_ids=[gone["id"]], reason="Not needed")
    await decision("d9", nodes=[ids["s9"]])

    async def plain(name: str, **more: Any) -> dict[str, Any]:
        record = await make_note(client, f"{name} {word}", "Observed", **more)
        ids[name] = record["id"]
        return record

    await plain("n1", nodes=[ids["s1"]], rule_ids=[ids["r1"]])
    await plain("n2", note_ids=[ids["d1"]])
    await plain("n3")
    old_note = await plain("n_old", nodes=[ids["s2"]])
    replacement = await ok(
        client,
        "supersede_note",
        replaces=[old_note["id"]],
        text=f"n_new {word}",
        why="Measured again",
        keep_links=True,
    )
    ids["n_new"] = replacement["replacement"]["id"]
    gone_note = await plain("n_gone", nodes=[ids["s1"]])
    await ok(client, "withdraw_note", note_ids=[gone_note["id"]], reason="A slip")
    await plain("n9", nodes=[ids["s9"]])
    confirm_on_board("note", {"id": ids["d1"]}, "Marie", project=project)
    confirm_on_board("note", {"id": ids["n1"]}, "Marie", project=project)
    return ids
