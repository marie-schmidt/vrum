"""M52 on the board: one standard review.

The page of what needs a person is an inbox of compact cards in groups the person chooses (Level,
Topic, Decision: the server says every row's level, topic and decision and the board only puts the
rows in those groups), every card ends with the same one-line bar of verbs (Approve, Request
changes, Reject, the choices of a question, Lift, Drop, Block: only the verbs that do something for
that kind), and Open goes to a full-screen review page of that change, never to the item's page.
Nothing on either page explains itself in a sentence, no letter, count line or diff mark is left,
and the choices that act on a change (Changes or Whole plan, Unified or Split) are on the review
page, where each is proved to change its lines.

Driven in a real headless browser and compared with what a real MCP client reads. Every test makes
an account and a project of its own."""

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Locator, Page, ViewportSize, expect

from tests.board_helpers import (
    SLOW,
    browser_page,
    go,
    on_page,
    open_review,
    sentences,
)
from tests.helpers import connect, ok, reindexed
from tests.test_board_needs_integration import leave_waiting, rows_of
from tests.test_board_review_assist_integration import three_proposals
from tests.test_checks_integration import as_person
from tests.world import Person, Project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

WIDE: ViewportSize = {"width": 1280, "height": 900}
NARROW: ViewportSize = {"width": 390, "height": 900}
# The titles of the groups of the server's own rows: not the routine moves, not the plans that wait.
PLAIN_GROUP_TITLES = (
    "#needs-body section.needs-group:not([data-group=routine]):not([data-group=agent]) .group-title"
)

# The verbs each kind of change has, and no others.
VERBS = {
    "plan": ["approve", "changes", "reject"],
    "placement": ["approve", "reject"],
    "note": ["approve", "reject"],
    "check": ["approve"],
    "gate": ["lift", "drop"],
    "lapse": ["block", "drop"],
}
BARS = {
    "plan": "approve",
    "placement": "approve",
    "note": "approve",
    "check": "approve",
    "ask": "answer",
    "gate": "look_into",
    "lapse": "look_into",
}


@dataclass
class World:
    """One of everything waiting for a person, as an agent leaves it."""

    waiting: Any
    refs: dict[str, str]
    routine: list[int]
    other: list[int]
    plan: int
    agent_plan: int
    gate: int
    gated: str


def change_of(row: dict[str, Any]) -> str:
    """The address of a row's change: the card's `data-change`."""
    if row["plan"]:
        return f"plan:{row['plan']['id']}"
    if row["placement"]:
        return f"placement:{row['placement']['id']}"
    if row["ask"]:
        return f"ask:{row['ask']['id']}"
    if row["gate"]:
        return f"gate:{row['gate']['id']}"
    return f"{row['kind']}:{row['node']}"


def look_of(row: dict[str, Any]) -> str:
    """What kind of change a row is, as the bar's `data-kind` says it."""
    return str(row["kind"])


async def full_world(agent: Client, project: Project, owner: Person) -> World:
    """Questions, a check, a note, a claim that ran out, a refused approval, two suggested moves
    the server calls routine and one it does not, a plan that waits for a person and a plan that
    waits for its agent, under epics about different things."""
    waiting = await leave_waiting(agent, project, owner, lapse=True)
    refs, routine, other = await three_proposals(agent)
    made = await ok(
        agent,
        "create_plan",
        draft=True,
        title="The winter plan",
        items=[
            {
                "key": "s",
                "kind": "story",
                "title": "Order heated docks",
                "parent": refs["billing"],
                "size": "m",
            },
            {"key": "t", "kind": "task", "title": "Pick a supplier", "parent": "s", "size": "xs"},
            {"key": "u", "kind": "task", "title": "Place the order", "parent": "s"},
        ],
    )
    asked = await ok(
        agent,
        "create_plan",
        draft=True,
        title="A plan the agent must revise",
        items=[{"key": "s", "kind": "story", "title": "Another story", "parent": refs["mail"]}],
    )
    sent = as_person(
        owner,
        project,
        "request_plan_changes",
        plan=asked["plan"],
        revision=1,
        summary="Needs a size.",
        comments=[],
    )
    assert sent.status == 200, sent.body
    gated = await ok(
        agent, "create_node", actor="x", kind="task", title="Launch the pilot", parent=waiting.story
    )
    gate = await ok(
        agent,
        "add_gate",
        node=gated["ref"],
        kind="approval",
        question="May the pilot start Monday?",
    )
    ask = [
        r
        for r in rows_of(await ok(agent, "needs_a_person"))
        if r["ask"] and r["node"] == gated["ref"]
    ][0]["ask"]["id"]
    refused = as_person(
        owner, project, "answer_ask", ask=ask, answer="no", comment="Not before the audit."
    )
    assert refused.status == 200, refused.body
    await reindexed(agent)
    return World(
        waiting,
        refs,
        routine,
        other,
        int(made["plan"]),
        int(asked["plan"]),
        int(gate["id"]),
        gated["ref"],
    )


async def rows(agent: Client) -> list[dict[str, Any]]:
    return rows_of(await ok(agent, "needs_a_person"))


def card(page: Page, change: str) -> Locator:
    return page.locator(f"#needs-body li.needs-row[data-change='{change}']")


async def open_routine(page: Page) -> None:
    """Open the closed group of routine moves, once the list is drawn and every move is read."""
    await page.wait_for_function(
        "() => document.querySelectorAll('#needs-body li.needs-row').length > 0"
        " || document.querySelector('#needs-empty')",
        timeout=SLOW,
    )
    await expect(page.locator("#needs-body li.needs-row[data-reading='true']")).to_have_count(
        0, timeout=SLOW
    )
    group = page.locator("details.routine-group")
    if await group.count() and await group.get_attribute("open") is None:
        await group.locator("summary").click()


async def the_list(page: Page, w: Any, agent: Client, group: str = "level") -> None:
    await w.open("#/needs" if group == "level" else f"#/needs?group={group}")
    await on_page(page, "needs")
    wanted = len(await rows(agent))
    await expect(page.locator("#needs-body li.needs-row")).to_have_count(wanted, timeout=SLOW)
    await expect(page.locator("#needs-body li.needs-row[data-reading='true']")).to_have_count(
        0, timeout=SLOW
    )


async def groups_drawn(page: Page) -> dict[str, list[str]]:
    found: dict[str, list[str]] = await page.evaluate(
        """() => Object.fromEntries(
            [...document.querySelectorAll('#needs-body section.needs-group')].map((s) => [
            s.dataset.group,
            [...s.querySelectorAll('li.needs-row')].map((r) => r.dataset.change),
        ]))"""
    )
    return found


# -- the groups ----------------------------------------------------------------------------------


async def test_the_groups_of_each_way_of_grouping_are_what_the_server_says_and_add_up() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        served = await rows(agent)
        total = (await ok(agent, "needs_a_person"))["total"]
        assert len(served) == total
        for row in served:
            assert row["level"] in {"plan", "epic", "story", "task", "note"}
            assert row["decision"] in {"approve", "answer", "look_into"}
        routine = {f"placement:{n}" for n in world.routine}
        page = w.page
        await the_list(page, w, agent)

        for by in ("level", "decision", "topic"):
            await w.open("#/needs" if by == "level" else f"#/needs?group={by}")
            await expect(page.locator("#needs-group")).to_have_value(by)
            await expect(page.locator("#needs-body li.needs-row")).to_have_count(
                total, timeout=SLOW
            )
            drawn = await groups_drawn(page)
            wanted: dict[str, list[str]] = {}
            for row in served:
                change = change_of(row)
                if change in routine:
                    continue
                key = {
                    "level": row["level"],
                    "decision": row["decision"],
                    "topic": row["topic"]["id"] if row["topic"] else "other",
                }[by]
                wanted.setdefault(key, []).append(change)
            # The groups are the server's words, every row once, none lost, and routine moves apart.
            assert {k: sorted(v) for k, v in drawn.items() if k not in ("routine", "agent")} == {
                k: sorted(v) for k, v in wanted.items()
            }, (by, drawn, wanted)
            assert sorted(drawn["routine"]) == sorted(routine)
            for section in await page.locator("#needs-body section.needs-group").all():
                assert await section.get_attribute("data-by") == by
            # The counts of the groups add up to the rows: no row is lost or drawn twice.
            counts = await page.locator(
                "#needs-body section.needs-group:not([data-group=agent]) .group-count"
            ).all_text_contents()
            assert sum(int(c) for c in counts) == total, counts
            every = await page.locator("#needs-body li.needs-row").evaluate_all(
                "els => els.map((e) => e.dataset.change)"
            )
            assert len(every) == len(set(every)) == total
            # Each group is headed by its icon and name, with its count.
            for section in await page.locator(
                "#needs-body section.needs-group:not([data-group=agent])"
            ).all():
                title = section.locator(".group-title").first
                await expect(title).not_to_have_text("")
            # A group with no rows is not drawn.
            assert all(v for k, v in drawn.items() if k != "agent")

        # Level: plans, epics, stories, tasks, notes in that order, with their words.
        await w.open("#/needs")
        titles = await page.locator(PLAIN_GROUP_TITLES).all_text_contents()
        names = {
            "plan": "Plans",
            "epic": "Epics",
            "story": "Stories",
            "task": "Tasks",
            "note": "Notes",
        }
        present = [
            k for k in names if any(r["level"] == k and change_of(r) not in routine for r in served)
        ]
        assert titles == [names[k] for k in present]
        # Decision: To approve, To answer, To look into.
        await w.open("#/needs?group=decision")
        titles = await page.locator(PLAIN_GROUP_TITLES).all_text_contents()
        assert titles == ["To approve", "To answer", "To look into"]
        # Topic: one group for each topic the server named, the label as its title, Other last.
        await w.open("#/needs?group=topic")
        labels = {r["topic"]["id"]: r["topic"]["label"] for r in served if r["topic"]}
        heads = page.locator(
            "#needs-body section.needs-group:not([data-group=routine]):not([data-group=agent])"
        )
        for section in await heads.all():
            key = await section.get_attribute("data-group")
            if key != "other":
                assert (await section.locator(".group-title").text_content()) == labels[str(key)]
        # The plan that waits for its agent is a closed group, not a sentence, in every way.
        for by in ("level", "topic", "decision"):
            await w.open("#/needs" if by == "level" else f"#/needs?group={by}")
            group = page.locator("section.needs-group[data-group=agent] details.agent-group")
            await expect(group).to_have_count(1, timeout=SLOW)
            await expect(group).not_to_have_attribute("open", "")
            await expect(group.locator("summary .group-count")).to_have_text("1")
            await expect(group.locator("li.waiting-row[data-plan]")).to_have_count(1)
            assert (await group.locator("a.change-title").get_attribute("href")) == (
                f"#/review?change=plan:{world.agent_plan}"
                + ("" if by == "level" else f"&group={by}")
            )
        # Choosing a way changes the address (Level says nothing) and nothing is stored.
        await page.locator("#needs-group").select_option("topic")
        assert (await page.evaluate("location.hash")) == "#/needs?group=topic"
        await page.locator("#needs-group").select_option("level")
        assert (await page.evaluate("location.hash")) == "#/needs"
        assert await page.evaluate("Object.keys(localStorage)") in ([], ["vrum.theme"])
        assert w.problems == []


async def test_no_letter_no_diffstat_no_counted_line_no_hunk_marks_and_no_toggle_on_the_list() -> (
    None
):
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        page = w.page
        await the_list(page, w, agent)
        await open_routine(page)
        for gone in (
            "#needs-counts",
            "#needs-view",
            ".change-letter",
            ".diffstat",
            ".change-summary",
            ".change-consequence",
            "#needs-waiting-agent",
            "button.change-open-all",
            "button.change-fold",
        ):
            await expect(page.locator(f"#needs-page {gone}")).to_have_count(0)
        text = await page.locator("#needs-page").inner_text()
        assert "@@" not in text
        assert not re.search(r"\b\d+ to (add|move)\b", text), text
        assert not re.search(r"Plan \d+: ", text) and not re.search(r"Proposal \d+: ", text)
        # The list has no toggle that could do nothing: no Unified or Split, no Changes or Whole.
        await expect(
            page.locator("#needs-page button[data-view], #needs-page button[data-mode]")
        ).to_have_count(0)
        # Pressing s does nothing on the list.
        await page.keyboard.press("s")
        assert (await page.evaluate("location.hash")) == "#/needs"
        # The kind chip carries the code and the title does not repeat it.
        plan = card(page, f"plan:{world.plan}")
        await expect(plan.locator(".change-kind")).to_contain_text(f"Plan {world.plan}")
        await expect(plan.locator("a.change-title")).to_have_text("The winter plan")
        assert w.problems == []


async def test_a_suggested_moves_title_has_no_code_and_the_codes_inside_it_are_links() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        (move,) = [
            r
            for r in await rows(agent)
            if r["placement"] and r["placement"]["id"] == world.other[0]
        ]
        refs = [one["ref"] for one in move["placement"]["items"]]
        page = w.page
        await the_list(page, w, agent)
        await open_routine(page)
        change = f"placement:{world.other[0]}"
        title = card(page, change).locator(".change-title")
        words = (await title.text_content() or "").strip()
        assert words.startswith("Move "), words
        assert f"Proposal {world.other[0]}" not in words and ":" not in words.split(" ")[0]
        # Every item code in it is an ordinary item link, to that item.
        links = title.locator("a.item-link[data-ref]")
        assert await links.evaluate_all("els => els.map((e) => e.dataset.ref)") == [
            *refs,
            move["placement"]["parent"],
        ]
        for link in await links.all():
            ref = str(await link.get_attribute("data-ref"))
            assert (await link.get_attribute("href")) == f"#/item?item={ref}&from=needs"
        # The kind chip says what it is and its code; Open is the link for the keyboard.
        await expect(card(page, change).locator(".change-kind")).to_contain_text(
            f"Proposal {world.other[0]}"
        )
        await expect(card(page, change).locator("a.change-open")).to_have_attribute(
            "aria-label", re.compile(rf"^Open Proposal {world.other[0]} Move ")
        )
        # Pressing its words (not a code) opens the review page.
        await title.click(position={"x": 4, "y": 6})
        await on_page(page, "review")
        assert (await page.evaluate("location.hash")).startswith(f"#/review?change={change}")
        h1 = page.locator("h1#review-title")
        assert (await h1.text_content() or "").strip().startswith("Move ")
        assert await h1.locator("a.item-link[data-ref]").evaluate_all(
            "els => els.map((e) => e.dataset.ref)"
        ) == [*refs, move["placement"]["parent"]]
        await expect(page.locator("#review-page .change-kind")).to_contain_text(
            f"Proposal {world.other[0]}"
        )
        await expect(page.locator("#crumb-page")).to_have_text(f"Proposal {world.other[0]}")
        assert w.problems == []


async def test_a_rail_entry_has_a_level_word_only_before_a_code_that_does_not_name_itself() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        served = await rows(agent)
        page = w.page
        await the_list(page, w, agent)
        entries = await page.evaluate(
            """() => [...document.querySelectorAll('nav#needs-rail a.rail-change')].map((a) => ({
                change: a.dataset.change || 'plan:' + a.dataset.plan,
                code: a.querySelector('.rail-code').textContent,
                level: a.querySelector('.rail-level')?.textContent ?? null,
            }))"""
        )
        assert len(entries) == len(served) + 1
        by_change = {one["change"]: one for one in entries}
        for row in served:
            one = by_change[change_of(row)]
            if re.match(r"[A-Za-z]+ \d", one["code"]):
                assert one["level"] is None, one
            else:
                assert one["level"] is not None and one["level"].lower() == row["level"], one
        # The plan, a move whose level is story and a question on a task, by name.
        assert by_change[f"plan:{world.plan}"]["level"] is None
        assert by_change[f"plan:{world.agent_plan}"]["level"] is None
        assert by_change[f"placement:{world.other[0]}"]["code"].startswith("Proposal ")
        assert by_change[f"placement:{world.other[0]}"]["level"] is None
        assert any(one["code"].startswith("Q-") and one["level"] for one in entries)
        # The drop-down says the same, in one line each.
        options = await page.evaluate(
            "() => [...document.querySelectorAll('select#needs-rail-select option[value]')]"
            ".map((o) => o.textContent)"
        )
        assert options
        for text in options:
            assert not re.search(r"\b(Plan|Story|Epic|Task|Note) (Plan|Proposal|Gate) \d", text), (
                text
            )
        assert w.problems == []


# -- Open goes to the review page ------------------------------------------------------------------


async def test_open_goes_to_the_review_page_of_every_kind_which_shows_what_the_tools_say() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        served = await rows(agent)
        page = w.page
        await the_list(page, w, agent)
        await open_routine(page)
        seen = set()
        for row in served:
            change = change_of(row)
            await w.open("#/needs")
            await on_page(page, "needs")
            await open_routine(page)
            await open_review(page, change)
            review = page.locator("#review-page")
            hash_ = await page.evaluate("location.hash")
            assert hash_.startswith("#/review?change=") and "#/item" not in hash_
            await expect(review).to_have_attribute("data-change", change)
            await expect(review).to_have_attribute("data-level", row["level"])
            await expect(review).to_have_attribute("data-decision", row["decision"])
            await expect(page.locator("h1#review-title")).to_be_visible()
            for part in ("changes",):
                await expect(review.locator(f"section[data-part={part}]")).to_have_count(1)
            seen.add(row["kind"])

            if row["plan"]:
                (plan,) = (await ok(agent, "list_plans", plan=row["plan"]["id"]))["items"]
                lines = review.locator(".diff-line.diff-item-line[data-sign='+'][data-ref]")
                await expect(lines).to_have_count(len(plan["tree"]))
                assert await lines.evaluate_all("els => els.map((e) => e.dataset.ref)") == [
                    one["ref"] for one in plan["tree"]
                ]
                await expect(
                    review.locator("section[data-part=checks] li.check-line")
                ).to_have_count(len([c for c in plan["checks"] if c["state"] != "ok"]))
                await expect(review.locator("section[data-part=context]")).to_have_count(0)
                await expect(page.locator("#review-item")).to_have_count(0)
            elif row["placement"]:
                served_move = [
                    p
                    for p in (await ok(agent, "list_placements", status="proposed"))["items"]
                    if p["id"] == row["placement"]["id"]
                ][0]
                plus = review.locator(".diff-line[data-sign='+'][data-ref]:not([data-part])")
                assert await plus.evaluate_all("els => els.map((e) => e.dataset.ref)") == [
                    one["ref"] for one in served_move["items"]
                ]
                scored = [s for s in served_move["scores"] if s["similarity"] is not None]
                await expect(review.locator(".diff-score")).to_have_count(len(scored))
                await expect(review.locator(".needs-move-reason")).to_have_text(
                    served_move["reason"]
                )
            elif row["ask"]:
                ask = row["ask"]
                assert (await review.locator("p.review-question").text_content()) == ask["question"]
                offered = await review.locator("ul.ask-offered li").all_text_contents()
                assert [o.lower() for o in offered] == [c.lower() for c in (ask["choices"] or [])]
                await expect(review.locator("[data-fact=effect]")).to_be_visible()
                await expect(review.locator("section[data-part=conversation]")).to_contain_text(
                    "Asked by"
                )
            elif row["kind"] == "check":
                node = await ok(agent, "get_node", node=row["node"])
                lines = review.locator("li.review-check")
                await expect(lines).to_have_count(len(node["checks"]))
                waiting = [
                    c["text"] for c in node["checks"] if c["person_only"] and not c["checked"]
                ]
                assert (
                    await review.locator(
                        "li.review-check[data-waiting=true] .diff-check-words"
                    ).all_text_contents()
                    == waiting
                )
            elif row["kind"] == "note":
                for note_id in row["notes"]:
                    note = await ok(agent, "get_note", note_id=note_id)
                    block = review.locator(f".review-note[data-note='{note_id}']")
                    assert (await block.locator(".note-text").text_content()) == note["text"]
                    await expect(block).to_contain_text(note["why"])
            elif row["gate"]:
                assert (
                    await review.locator(".review-gate .needs-gate-words").text_content() or ""
                ).endswith(row["gate"]["words"])
                await expect(review.locator(".review-gate")).to_contain_text(
                    "Not before the audit."
                )
            else:
                await expect(review.locator("li.review-claim")).to_have_count(row["count"])
            # For work, the context says where it sits and the way to its own page is Open <code>.
            if not row["plan"]:
                await expect(review.locator("section[data-part=context]")).to_have_count(1)
                await expect(
                    review.locator("section[data-part=context] ol.context-path li")
                ).not_to_have_count(0)
                item = page.locator("a#review-item")
                await expect(item).to_have_attribute("href", re.compile(r"^#/item\?item="))
        assert {"ask", "check", "note", "lapse", "gate", "plan", "placement"} <= seen

        # Back goes to the list the person came from, and the crumb names the change.
        await w.open("#/needs?group=decision")
        await open_routine(page)
        await open_review(page, f"plan:{world.plan}")
        await expect(page.locator("#crumb-page")).to_have_text(f"Plan {world.plan}")
        await expect(page.locator("nav.crumbs li.crumb-view a")).to_have_text("Needs a person")
        await expect(page.locator("#back-to-view")).to_be_hidden()
        await page.locator("#review-back").click()
        await on_page(page, "needs")
        assert (await page.evaluate("location.hash")) == "#/needs?group=decision"
        await expect(page.locator("#sidebar-needs-count")).not_to_have_text("")
        # The work's own page is reached from the review page and nowhere else on these two pages.
        await open_review(page, f"ask:{world.waiting.numbers['choice']}")
        await page.locator("#review-item").click()
        await on_page(page, "item")
        assert w.problems == []


async def test_an_address_names_the_change_and_one_that_is_not_there_says_so_in_a_short_line() -> (
    None
):
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        page = w.page
        await w.open("#/review?change=ask:999999")
        await expect(page.locator("#review-missing")).to_have_text(
            "That change is not here", timeout=SLOW
        )
        await expect(page.locator("#review-back")).to_be_visible()
        await w.go("#/review?change=nonsense")
        await expect(page.locator("#review-missing")).to_have_text("That change is not here")
        await w.go("#/review")
        await expect(page.locator("#review-missing")).to_have_text("That change is not here")
        # A plan that waits for its agent is in no row of the list and is read, read only. An
        # address loaded by itself has no steps.
        await go(page, project.address, f"#/review?change=plan:{world.agent_plan}")
        await on_page(page, "review")
        await expect(page.locator("#review-page")).to_have_attribute("data-in-list", "false")
        await expect(page.locator(".plan-state[data-state=changes_requested]")).to_be_visible()
        await expect(page.locator("#review-bar")).to_have_count(0)
        await expect(page.locator("button.verdict")).to_have_count(0)
        await expect(page.locator("nav.review-steps")).to_have_count(0)
        # A change that is in the list, reached by address alone, has its bar and no steps.
        await go(page, project.address, f"#/review?change=plan:{world.plan}")
        await on_page(page, "review")
        await expect(page.locator("#review-bar button.verdict")).to_have_count(3)
        await expect(page.locator("nav.review-steps")).to_have_count(0)
        assert w.problems == []


# -- the sentence rule -------------------------------------------------------------------


async def test_nothing_on_the_list_the_rail_or_a_review_page_explains_itself_in_a_sentence() -> (
    None
):
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        served = await rows(agent)
        page = w.page
        for by in ("level", "topic", "decision"):
            await the_list(page, w, agent, by)
            await open_routine(page)
            await page.locator("section[data-group=agent] summary").click()
            assert await sentences(page) == [], (by, await sentences(page))
        for row in served:
            await w.open("#/needs")
            await open_routine(page)
            await open_review(page, change_of(row))
            await expect(page.locator("#review-page section[data-part=changes]")).to_be_visible()
            found = await sentences(page)
            assert found == [], (change_of(row), found)
        await w.go(f"#/review?change=plan:{world.agent_plan}")
        await on_page(page, "review")
        assert await sentences(page) == []
        assert w.problems == []


async def test_no_dialog_a_decision_opens_explains_itself_either() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        page = w.page
        await the_list(page, w, agent)
        await open_routine(page)

        async def opened(change: str, verb: str, dialog: str) -> None:
            await card(page, change).locator(f"button.verdict[data-verb={verb}]").first.click()
            box = page.locator(f"dialog{dialog}")
            await expect(box).to_be_visible()
            assert await sentences(page, ["dialog[open]"]) == [], (change, verb)
            # A dialog is a heading, labelled fields and buttons.
            await expect(box.locator("h2")).to_have_count(1)
            await page.keyboard.press("Escape")
            await expect(box).to_have_count(0)

        await opened(f"plan:{world.plan}", "reject", "#reject-dialog")
        await opened(f"plan:{world.plan}", "changes", "#changes-dialog")
        await opened(f"placement:{world.other[0]}", "reject", "#reject-dialog")
        await opened(f"gate:{world.gate}", "lift", "#lift-gate")
        await opened(f"gate:{world.gate}", "drop", "#drop-dialog")
        await opened(f"lapse:{world.waiting.lapsed['ref']}", "block", "#block-dialog")
        await opened(f"lapse:{world.waiting.lapsed['ref']}", "drop", "#drop-dialog")
        await opened(f"ask:{world.waiting.numbers['free']}", "answer", "#answer-ask")
        await opened(f"ask:{world.waiting.numbers['approval']}", "reject", "#reject-dialog")
        await opened(f"note:{world.waiting.noted['ref']}", "approve", "")
        await opened(f"note:{world.waiting.noted['ref']}", "reject", "#reject-dialog")
        assert w.problems == []


# -- the bar -----------------------------------------------------------------------------


async def bar_facts(bar: Locator) -> dict[str, Any]:
    found: dict[str, Any] = await bar.evaluate(
        """(el) => {
            const items = [...el.querySelectorAll('button.verdict, button.decision-comment')];
            return {
                tops: items.map((b) => Math.round(b.getBoundingClientRect().top)),
                verbs: [...el.querySelectorAll('button.verdict')].map((b) => b.dataset.verb),
                answers: [...el.querySelectorAll('button.verdict')]
                    .map((b) => b.dataset.answer ?? null),
                comment: el.querySelectorAll('button.decision-comment').length,
                first: el.querySelector('button.verdict')?.classList.contains('primary'),
                wrap: getComputedStyle(el).flexWrap,
            };
        }"""
    )
    return found


async def test_the_bar_is_one_row_for_every_kind_and_has_only_that_kinds_verbs() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        world = await full_world(agent, project, owner)
        served = await rows(agent)
        page = w.page
        await page.set_viewport_size(WIDE)
        await the_list(page, w, agent)
        await open_routine(page)
        kinds: Counter[str] = Counter()
        for row in served:
            change = change_of(row)
            kind = look_of(row)
            verbs: list[str]
            answers: list[Any] | None
            if kind == "ask":
                ask = row["ask"]
                if ask["kind"] == "approval":
                    verbs, answers = ["approve", "reject"], ["yes", "no"]
                elif ask["choices"]:
                    verbs, answers = ["answer"] * len(ask["choices"]), ask["choices"]
                else:
                    verbs, answers = ["answer"], [None]
            else:
                verbs, answers = VERBS[kind], None
            bars = card(page, change).locator("div.decision-bar")
            await expect(bars).to_have_count(1)
            bar = bars.first
            await expect(bar).to_have_attribute("data-bar", BARS[kind])
            await expect(bar).to_have_attribute("data-kind", kind)
            facts = await bar_facts(bar)
            assert facts["verbs"] == verbs, (change, facts)
            if answers is not None:
                assert facts["answers"] == answers, (change, facts)
            assert facts["first"] is True
            assert facts["wrap"] == "nowrap"
            # One row: every button of the bar is at the same height, within two pixels.
            assert max(facts["tops"]) - min(facts["tops"]) <= 2, (change, facts)
            assert facts["comment"] == (1 if kind in ("ask", "check") else 0)
            # Every verb has a hover card, which keyboard focus opens too.
            first = bar.locator("button.verdict").first
            await first.focus()
            await expect(page.locator(".hovercard:visible")).to_have_count(1)
            await page.keyboard.press("Escape")
            kinds[kind] += 1
            # The server's words go in one place, outside the row.
            await expect(card(page, change).locator("p.decision-problem")).to_have_count(1)
        assert set(kinds) == set(BARS)

        # The same bar on the review page: one row, kept in view at the bottom.
        for change in (
            f"plan:{world.plan}",
            f"ask:{world.waiting.numbers['choice']}",
            f"gate:{world.gate}",
        ):
            await w.open("#/needs")
            await open_routine(page)
            await open_review(page, change)
            bar = page.locator("#review-bar div.decision-bar")
            await expect(bar).to_have_count(1)
            facts = await bar_facts(bar)
            assert max(facts["tops"]) - min(facts["tops"]) <= 2, facts
            await expect(page.locator("#review-bar")).to_have_css("position", "sticky")
        assert w.problems == []


# -- long titles are somebody's words -----------------------------------------------------


LONG = "Cover every one of the busiest stations before the first frost arrives"


async def test_long_titles_details_and_choices_are_theirs_and_never_read_as_sentences() -> None:
    from tests.test_board_plan_review_integration import a_revision, asked
    from tests.test_plan_review_integration import drafted

    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic = await ok(agent, "create_node", actor="x", kind="epic", title=f"Epic: {LONG}")
        inbox = await ok(agent, "create_node", actor="x", kind="epic", title="Inbox")
        loose = [
            await ok(
                agent,
                "create_node",
                actor="x",
                kind="story",
                title=f"{LONG}, number {n}",
                parent=inbox["ref"],
            )
            for n in (1, 2)
        ]
        await ok(
            agent,
            "propose_placement",
            items=[s["ref"] for s in loose],
            parent=epic["ref"],
            reason=LONG,
        )
        story = await ok(
            agent, "create_node", actor="x", kind="story", title=LONG, parent=epic["ref"]
        )
        task = await ok(
            agent, "create_node", actor="x", kind="task", title=LONG, parent=story["ref"]
        )
        await ok(agent, "claim_node", node=task["ref"])
        choices = [
            "Two zones: the centre and the outer districts",
            "Keep waiting for the city's template",
        ]
        await ok(
            agent,
            "finish_work",
            node=task["ref"],
            outcome="blocked",
            note="Need a person.",
            ask={"question": f"{LONG}?", "choices": choices},
        )
        number, refs = await drafted(agent, title=LONG)
        asked(
            project, owner, number, 1, LONG, [{"item": refs["s2"], "field": "title", "text": LONG}]
        )
        await a_revision(
            agent,
            number,
            refs,
            LONG,
            s2={"title": f"{LONG}, again", "body": f"{LONG}.\n{LONG}."},
        )
        await reindexed(agent)
        page = w.page
        served = await rows(agent)
        for by in ("level", "topic", "decision"):
            await the_list(page, w, agent, by)
            await open_routine(page)
            found = await sentences(page)
            assert found == [], (by, found)
        for row in served:
            await w.open("#/needs")
            await open_routine(page)
            await open_review(page, change_of(row))
            await expect(page.locator("#review-page section[data-part=changes]")).to_be_visible()
            for view in ("unified", "split"):
                if row["plan"] or row["placement"]:
                    await page.locator(f"#review-view button[data-view={view}]").click()
                found = await sentences(page)
                assert found == [], (change_of(row), view, found)
        assert w.problems == []
