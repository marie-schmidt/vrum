"""M50 on the board: review assistance on the page of what needs a person.

On top of the review of M49 (every decision drawn as a change), the page points at the unusual and
saves a trip to the item: a plan shows its three checks above its diff (a circle, a task with no
size, a story that may repeat work that exists) with a flag beside each item they name; a suggested
move shows how alike each moved item is to the parent it would go under, as git shows a rename
(R71); the moves the server calls routine are one closed group at the end, every one still a whole
card; a split view puts the old and the new state side by side on a wide window; and a note can be
confirmed and a check ticked on its card, through the very operations the item's page calls.

Driven in a real headless browser and compared with what a real MCP client reads: the checks equal
`list_plans(plan).checks`, the scores equal `suggest_parent` and `list_placements`, an unavailable
search is shown as not checked and never as no duplicates, grouping hides no warning, split and
unified show the same lines, confirming and ticking in place record the signed-in person exactly as
the item's page does, and a viewer has no write control. Every test makes an account and a project
of its own."""

import hashlib
import math
import re
from collections import Counter
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Locator, expect

from tests.board_context import board_refusal, choose_in_menu, dialog, refusal_in
from tests.board_helpers import (
    SLOW,
    browser_page,
    hold_back_news,
    on_page,
    open_item,
    open_review,
    plain,
)
from tests.board_layout import SCREENS, Screen, blank_page, contrast, layout_context, settle, tab_to
from tests.board_m14 import spoken
from tests.helpers import connect, embeddings_stopped, ok, refused, reindexed, unique_word
from tests.test_board_needs_integration import DECIDING, no_overflow, open_routine, row
from tests.test_board_review_integration import card_of, item_lines, refs_of
from tests.test_checks_integration import A_TOKEN, as_person, define, words_of
from tests.test_placement_integration import a_sorted_project
from tests.world import join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

STATE_WORDS = {"warning": "Warning", "failed": "Failed", "unavailable": "Not checked"}


def ordinal(number: int) -> str:
    tens = number % 100
    suffix = "th" if 10 <= tens <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def percent(similarity: float) -> int:
    """Git's similarity index: a whole number, rounded down, from 0 to 100 (a similarity below zero
    reads as 0%)."""
    return max(0, min(100, math.floor(similarity * 100)))


def code(ref: str) -> str:
    kind, number = ref.split(":")
    return f"{kind[0].upper()}-{number}"


async def plan_that_repeats_a_story(agent: Client, epic: str, title: str) -> dict[str, Any]:
    """A drafted plan under an existing epic: a story that has the same words as one that exists
    (so it may be a duplicate), a task with no size and a task with one."""
    made: dict[str, Any] = await ok(
        agent,
        "create_plan",
        draft=True,
        title="The same again",
        items=[
            {"key": "s1", "kind": "story", "title": title, "parent": epic, "size": "m"},
            {"key": "t1", "kind": "task", "title": "A task nobody sized", "parent": "s1"},
            {
                "key": "t2",
                "kind": "task",
                "title": "A task with a size",
                "parent": "s1",
                "size": "s",
            },
            {
                "key": "s2",
                "kind": "story",
                "title": " ".join(unique_word() for _ in range(4)),
                "parent": epic,
                "size": "l",
            },
        ],
    )
    return made


async def checks_of(agent: Client, number: int) -> list[dict[str, Any]]:
    (plan,) = (await ok(agent, "list_plans", plan=number))["items"]
    found: list[dict[str, Any]] = plan["checks"]
    return found


# -- the checks of a plan ----------------------------------------------------------------------


async def test_plan_checks_equal_the_servers_and_flags_sit_beside_the_items_they_name() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    title = " ".join(unique_word() for _ in range(4))
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Stations")
        existing = await ok(
            agent, "create_node", actor="x", kind="story", title=title, parent=epic["ref"]
        )
        await reindexed(agent)
        made = await plan_that_repeats_a_story(agent, epic["ref"], title)
        number = made["plan"]
        refs = {c["key"]: c["ref"] for c in made["created"]}
        checks = await checks_of(agent, number)
        assert [c["kind"] for c in checks] == ["circle", "unsized", "duplicate"]
        assert [c["state"] for c in checks] == ["ok", "warning", "warning"]
        unsized_before = (await ok(agent, "list_nodes", query="no:size"))["total"]

        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        # Something to weigh shows in the head of the change and in the rail, and nowhere in a
        # count of the page.
        listed = card_of(page, f"plan:{number}")
        await expect(listed.locator("li[data-fact=warnings].change-warning")).to_have_text(
            "2 warnings", timeout=SLOW
        )
        rail = page.locator(f"nav#needs-rail a.rail-change[data-change='plan:{number}']")
        await expect(rail).to_have_attribute("data-warning", "true")
        await expect(rail.locator(".rail-warning")).to_have_count(1)
        option = page.locator("select#needs-rail-select option", has_text="The same again")
        assert "has something to weigh" in ((await option.text_content()) or "")
        await expect(page.locator("#needs-counts")).to_have_count(0)
        # The checks themselves are on the review page, above the diff.
        await open_review(page, f"plan:{number}")
        card = page.locator("#review-page")
        await expect(card.locator("section.change-checks")).to_be_visible(timeout=SLOW)
        await expect(card.locator("li[data-fact=warnings].change-warning")).to_have_text(
            "2 warnings"
        )
        lines = card.locator("section.change-checks li.check-line")
        # One line for each check that did not pass, in the server's order, with its words.
        await expect(lines).to_have_count(2)
        shown = await lines.evaluate_all(
            """(els) => els.map((e) => [
                e.dataset.check, e.dataset.state,
                e.querySelector('.check-state').textContent,
                e.querySelector('.check-words').textContent,
                [...e.querySelectorAll('.check-finding')].map((f) => [
                    f.dataset.items, f.querySelector('.check-finding-words').textContent,
                    [...f.querySelectorAll('.check-related-item')].map((r) => [
                        r.dataset.ref, Number(r.dataset.similarity),
                        r.querySelector('.check-related-status').textContent,
                        r.querySelector('.check-related-similarity').textContent,
                        r.querySelector('a').textContent,
                    ]),
                ]),
            ])"""
        )
        wanted = []
        for check in checks:
            if check["state"] == "ok":
                continue
            wanted.append(
                [
                    check["kind"],
                    check["state"],
                    STATE_WORDS[check["state"]],
                    check["words"],
                    [
                        [
                            " ".join(finding["items"]),
                            finding["words"],
                            [
                                [
                                    r["ref"],
                                    pytest.approx(r["similarity"]),
                                    r["status"].replace("_", " ").capitalize(),
                                    f"{math.floor(r['similarity'] * 100 + 0.5)}% alike",
                                    f"{code(r['ref'])} {r['title']}",
                                ]
                                for r in finding["related"]
                            ],
                        ]
                        for finding in check["findings"]
                    ],
                ]
            )
        assert shown == wanted
        # The story that repeats the existing one is named, with the existing story as a link.
        duplicate = checks[2]
        (finding,) = duplicate["findings"]
        assert finding["items"] == [refs["s1"]]
        assert existing["ref"] in [r["ref"] for r in finding["related"]]
        link = card.locator(f".check-related-item[data-ref='{existing['ref']}'] a.item-link")
        await expect(link).to_have_attribute(
            "href", re.compile(rf"item=story:{existing['ref'][6:]}")
        )
        # Everything that passed is one quiet line.
        await expect(card.locator("p.check-ok")).to_have_text("No circles")

        # A flag beside each item a finding names, and nothing else: the unsized task and the
        # story that may repeat existing work.
        flags = await card.locator(".diff-flag").evaluate_all(
            "els => els.map((e) => [e.closest('.diff-line').dataset.ref, e.dataset.check, "
            "e.textContent.trim(), e.getAttribute('aria-label')])"
        )
        expected = [
            [ref, check["kind"], text(check, finding), finding["words"]]
            for check in checks
            if check["state"] != "ok"
            for finding in check["findings"]
            for ref in finding["items"]
        ]
        assert sorted(flags) == sorted(expected)
        assert [f[:3] for f in flags if f[1] == "unsized"] == [[refs["t1"], "unsized", "no size"]]
        # The flag sits on the line of the item and the line still says what it said.
        line = card.locator(f".diff-line[data-ref='{refs['t1']}']")
        await expect(line).to_contain_text("A task nobody sized")
        await expect(line.locator("a.diff-item")).to_have_count(1)
        # Nothing was given a size: the check says so, and the graph agrees.
        assert (await ok(agent, "get_node", node=refs["t1"]))["size"] is None
        assert (await ok(agent, "list_nodes", query="no:size"))["total"] == unsized_before

        # A warning never stops a decision.
        await page.locator("#review-bar button.verdict[data-verb=approve]").click()
        await expect(page.locator("#review-page")).to_have_count(0, timeout=SLOW)
        approved = (await ok(agent, "list_plans", status="approved"))["items"][0]
        assert approved["id"] == number and approved["decided_by"] == "Priya Raman"
        assert w.problems == []


def text(check: dict[str, Any], finding: dict[str, Any]) -> str:
    """The short words of a flag: the same as the page says."""
    if check["kind"] == "unsized":
        return "no size"
    if check["kind"] == "duplicate":
        return f"may repeat {code(finding['related'][0]['ref'])}"
    return "in a circle"


async def test_a_plan_with_nothing_to_weigh_shows_one_quiet_line_and_no_warning() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Stations")
        await reindexed(agent)
        made = await ok(
            agent,
            "create_plan",
            draft=True,
            title="A clean plan",
            items=[
                {
                    "key": "s",
                    "kind": "story",
                    "title": " ".join(unique_word() for _ in range(4)),
                    "parent": epic["ref"],
                    "size": "m",
                },
                {"key": "t", "kind": "task", "title": "Do it", "parent": "s", "size": "s"},
            ],
        )
        assert {c["state"] for c in await checks_of(agent, made["plan"])} == {"ok"}
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        listed = card_of(page, f"plan:{made['plan']}")
        await expect(listed).to_be_visible(timeout=SLOW)
        await expect(listed.locator(".change-warning")).to_have_count(0)
        await open_review(page, f"plan:{made['plan']}")
        card = page.locator("#review-page")
        await expect(card.locator("p.check-ok .check-ok-part")).to_have_text(
            ["No circles", "Every task sized", "No repeated work"], timeout=SLOW
        )
        await expect(card.locator("li.check-line")).to_have_count(0)
        await expect(card.locator(".change-warning")).to_have_count(0)
        await expect(card.locator(".diff-flag")).to_have_count(0)
        await page.locator("#review-back").click()
        await on_page(page, "needs")
        await expect(page.locator("nav#needs-rail .rail-warning")).to_have_count(0)
        assert w.problems == []


async def test_search_by_meaning_being_away_is_shown_as_not_checked_and_the_move_says_why() -> None:
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        made = await plan_that_repeats_a_story(agent, refs["billing"], "Issue the invoice")
        number = made["plan"]
        with embeddings_stopped():
            blind = {c["kind"]: c for c in await checks_of(agent, number)}
            # Made while the service is away: not indexed, so it cannot be ranked for a parent.
            fresh = await ok(
                agent,
                "create_node",
                actor="x",
                kind="story",
                title="Pay for the late invoice",
                parent=refs["inbox"],
            )
            proposed = await a_proposal(agent, [fresh["ref"]], refs["billing"], "It is money.")
            move = await read_proposal(agent, proposed)
            (score,) = move["scores"]
            said = move["scores_note"] or score["note"]
            assert blind["duplicate"]["state"] == "unavailable" and said
            assert score["similarity"] is None and move["routine"] is False
            async with browser_page(project.address, token=project.key, name=None) as w:
                page = w.page
                await w.open("#/needs")
                await on_page(page, "needs")
                listed = card_of(page, f"plan:{number}")
                await expect(listed.locator(".change-warning")).to_contain_text(
                    "not checked", timeout=SLOW
                )
                await expect(listed.locator(".change-warning")).to_contain_text("1 warning")
                await open_review(page, f"plan:{number}")
                card = page.locator("#review-page")
                line = card.locator("li.check-line[data-check='duplicate']")
                await expect(line).to_have_attribute("data-state", "unavailable", timeout=SLOW)
                # As plain as a warning, with the server's words, and never "no repeated work".
                await expect(line.locator(".check-state")).to_have_text("Not checked")
                await expect(line.locator(".check-words")).to_have_text(blind["duplicate"]["words"])
                ok_line = (await card.locator("p.check-ok").text_content()) or ""
                assert "repeated" not in ok_line and "No circles" in ok_line
                # The size check does not depend on it.
                await expect(card.locator("li.check-line[data-check='unsized']")).to_have_count(1)
                # A move that could not be scored says why, in the server's words, and is not
                # routine.
                await page.locator("#review-back").click()
                await on_page(page, "needs")
                moved = card_of(page, f"placement:{proposed}")
                await expect(moved).to_be_visible(timeout=SLOW)
                await expect(page.locator("details.routine-group")).to_have_count(0)
                await open_review(page, f"placement:{proposed}")
                move_page = page.locator("#review-page")
                await expect(move_page.locator(".diff-score")).to_have_count(0)
                # The server's words, as they are when the page asks (the job that keeps the index
                # may have noticed the outage since the first read, which changes the sentence).
                where = move_page.locator("p.change-scores-note, .diff-score-note")
                await expect(where.first).to_be_visible()
                later = await read_proposal(agent, proposed)
                words = {said, later["scores_note"] or later["scores"][0]["note"]}
                assert ((await where.first.text_content()) or "") in words
                reason = (await move_page.locator("p.change-routine-why").text_content()) or ""
                assert reason in {move["routine_why"], later["routine_why"]}
                assert w.problems == []


# -- the scores of a move --------------------------------------------------------------------------


async def a_proposal(agent: Client, items: list[str], parent: str, reason: str) -> int:
    made = await ok(agent, "propose_placement", items=items, parent=parent, reason=reason)
    return int(made["id"])


async def read_proposal(agent: Client, number: int) -> dict[str, Any]:
    page = await ok(agent, "list_placements", status="proposed")
    (one,) = [p for p in page["items"] if p["id"] == number]
    found: dict[str, Any] = one
    return found


async def test_move_scores_equal_the_parent_suggestions_and_the_lowest_is_marked() -> None:
    project = make_project(register())
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        items = [refs["loose_pay"], refs["loose_mail"], refs["loose_login"]]
        number = await a_proposal(agent, items, refs["billing"], "All three are about money.")
        served = await read_proposal(agent, number)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(card_of(page, f"placement:{number}")).to_be_visible(timeout=SLOW)
        await open_review(page, f"placement:{number}")
        card = page.locator("#review-page")
        await expect(card.locator(".diff-score").first).to_be_visible(timeout=SLOW)

        # Each item's score is the one suggest_parent gives it for that parent, as a whole percent.
        asked = {}
        for ref in items:
            found = await ok(agent, "suggest_parent", node=ref, limit=10)
            ranked = [c["ref"] for c in found["suggestions"]]
            asked[ref] = (
                found["suggestions"][ranked.index(refs["billing"])]["score"],
                ranked.index(refs["billing"]) + 1,
                found["suggestions"][0]["ref"],
            )
        scores = {s["ref"]: s for s in served["scores"]}
        for ref in items:
            assert scores[ref]["similarity"] == pytest.approx(asked[ref][0], abs=1e-9)
        shown = await card.locator(".diff-score").evaluate_all(
            "els => els.map((e) => [e.dataset.ref, e.textContent, e.getAttribute('aria-label')])"
        )
        assert shown == [
            [
                ref,
                f"{percent(asked[ref][0])}%",
                f"similarity {percent(asked[ref][0])}%, ranks {ordinal(asked[ref][1])}",
            ]
            for ref in items
        ]
        # Where the parent is not first, the page says so and names the parent that is.
        for ref in items:
            rank = card.locator(f".diff-rank[data-ref='{ref}']")
            if asked[ref][1] == 1:
                await expect(rank).to_have_count(0)
                continue
            best = asked[ref][2]
            title = (await ok(agent, "get_node", node=best))["title"]
            await expect(rank).to_have_text(
                f"ranks {ordinal(asked[ref][1])}, best is {code(best)} {title}"
            )
        # The item with the lowest score is marked, and only when the scores differ.
        percents = {ref: percent(asked[ref][0]) for ref in items}
        low = min(percents.values())
        lowest = (
            [ref for ref in items if percents[ref] == low]
            if len(set(percents.values())) > 1
            else []
        )
        marked = await card.locator(".diff-lowest").evaluate_all(
            "els => els.map((e) => e.dataset.ref)"
        )
        assert marked == lowest
        # No git-style letter and no figure on the head: the scores are on the lines.
        await expect(card.locator(".change-letter")).to_have_count(0)
        # The server's reason for calling it routine or not, in one quiet line, whichever it is.
        assert served["routine"] is False
        await expect(card.locator("p.change-routine-why")).to_have_text(served["routine_why"])
        # A score is evidence, not approval: nothing moved and the buttons are as they were.
        assert (await ok(agent, "get_node", node=refs["loose_pay"]))["parent"] == refs["inbox"]
        await expect(page.locator("#review-bar").locator(DECIDING)).to_have_text(
            ["Approve", "Reject…"]
        )
        assert w.problems == []


async def test_a_move_is_drawn_once_it_has_been_read_and_never_as_an_unscored_move_first() -> None:
    project = make_project(register())
    async with browser_page(project.address, token=project.key, name=None) as w:
        async with connect(project.mcp_url, project.key) as agent:
            refs = await a_sorted_project(agent)
            number = await a_proposal(agent, [refs["loose_mail"]], refs["billing"], "Mail.")
        page = w.page

        async def slowly(route: Any) -> None:
            import asyncio

            await asyncio.sleep(2.5)
            await route.continue_()

        await page.route("**/tool/list_placements", slowly)
        await w.open("#/needs")
        card = card_of(page, f"placement:{number}")
        await expect(card).to_have_attribute("data-reading", "true", timeout=SLOW)
        await expect(card.locator(".diff-loading")).to_have_text("Reading…")
        # Nothing to decide and nothing to score until it has been read.
        await expect(card.locator(".diff-item-line")).to_have_count(0)
        await expect(card.locator(".diff-score")).to_have_count(0)
        await expect(card.locator(DECIDING)).to_have_count(0)
        await expect(card).not_to_have_attribute("data-reading", "true", timeout=SLOW)
        await expect(card.locator(".diff-item-line")).to_have_count(1)
        await expect(card.locator(DECIDING)).to_have_text(["Approve", "Reject…"])
        # The scores are on the review page, where the whole move is read.
        await open_review(page, f"placement:{number}")
        await expect(page.locator("#review-page .diff-score")).to_have_count(1, timeout=SLOW)
        assert w.problems == []


async def test_work_under_way_is_a_warning_and_keeps_a_move_out_of_the_routine_group() -> None:
    project = make_project(register())
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        number = await a_proposal(agent, [refs["loose_pay"]], refs["billing"], "Invoices.")
        assert (await read_proposal(agent, number))["routine"] is True
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        # Routine to begin with: it is in the group, which is closed.
        await expect(page.locator("details.routine-group")).to_have_count(1, timeout=SLOW)
        # Someone takes the item. The page hears of it, and the proposal comes out of the group.
        await ok(agent, "claim_node", node=refs["loose_pay"])
        served = await read_proposal(agent, number)
        (score,) = served["scores"]
        assert "is held by" in score["warning"] and served["routine"] is False
        assert served["routine_why"] == score["warning"]
        await expect(page.locator("details.routine-group")).to_have_count(0, timeout=SLOW)
        card = card_of(page, f"placement:{number}")
        await expect(card).to_be_visible()
        await expect(card.locator(".change-warning")).to_have_text("1 warning")
        rail = page.locator(f"nav#needs-rail a.rail-change[data-change='placement:{number}']")
        await expect(rail.locator(".rail-warning")).to_have_count(1)
        # The warning is on the line of the item, in the server's words, on the review page.
        await open_review(page, f"placement:{number}")
        whole = page.locator("#review-page")
        flag = whole.locator(".diff-line[data-sign='+'] .diff-flag[data-check='warning']")
        await expect(flag).to_have_text("Warning")
        await expect(flag).to_have_attribute("aria-label", f"Warning: {score['warning']}")
        await expect(whole.locator(".diff-flag-words")).to_have_text(score["warning"])
        await expect(whole.locator("p.change-routine-why")).to_have_text(served["routine_why"])
        assert w.problems == []


async def test_a_new_epic_has_no_scores_and_says_why_in_the_servers_words() -> None:
    project = make_project(register())
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        made = await ok(
            agent,
            "propose_placement",
            items=[refs["loose_pay"], refs["loose_mail"]],
            new_epic={"title": "Money and messages", "body": ""},
            reason="Two themes.",
        )
        served = await read_proposal(agent, made["id"])
        assert served["scores"] == [] and served["routine"] is False
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(card_of(page, f"placement:{made['id']}")).to_be_visible(timeout=SLOW)
        await expect(
            card_of(page, f"placement:{made['id']}").locator(".change-warning")
        ).to_have_count(0)
        await expect(page.locator("details.routine-group")).to_have_count(0)
        await open_review(page, f"placement:{made['id']}")
        card = page.locator("#review-page")
        await expect(card.locator("p.change-scores-note")).to_have_text(
            served["scores_note"], timeout=SLOW
        )
        await expect(card.locator(".diff-score")).to_have_count(0)
        await expect(card.locator(".change-letter")).to_have_count(0)
        await expect(card.locator(".diff-hunk").first).to_have_text("A new epic")
        assert w.problems == []


# -- the routine group -----------------------------------------------------------------------------


async def three_proposals(agent: Client) -> tuple[dict[str, str], list[int], list[int]]:
    """Two moves the server calls routine and one it does not. Returns the references, the
    routine proposals' numbers and the other's."""
    refs = await a_sorted_project(agent)
    first = await a_proposal(agent, [refs["loose_pay"]], refs["billing"], "Invoices.")
    second = await a_proposal(agent, [refs["loose_login"]], refs["access"], "A password.")
    third = await a_proposal(agent, [refs["loose_mail"]], refs["billing"], "Mail is money?")
    served = {n: await read_proposal(agent, n) for n in (first, second, third)}
    routine = [n for n, one in served.items() if one["routine"] is True]
    other = [n for n, one in served.items() if one["routine"] is False]
    assert len(routine) == 2 and other == [third], {n: o["routine_why"] for n, o in served.items()}
    return refs, routine, other


async def test_routine_moves_are_one_closed_group_that_opens_to_whole_cards() -> None:
    project = make_project(register())
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, routine, other = await three_proposals(agent)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(page.locator("details.routine-group")).to_have_count(1, timeout=SLOW)
        await expect(page.locator("li.needs-row[data-kind='placement']")).to_have_count(3)
        # The one that is not routine is outside the group; the group of routine ones is last.
        group = page.locator("details.routine-group")
        assert await group.locator("li.needs-row").evaluate_all(
            "els => els.map((e) => e.dataset.change)"
        ) == [f"placement:{n}" for n in routine]
        await expect(
            group.locator(f"li.needs-row[data-change='placement:{other[0]}']")
        ).to_have_count(0)
        await expect(card_of(page, f"placement:{other[0]}")).to_be_visible()
        sections = page.locator("#needs-body > section.needs-group")
        assert await sections.last.get_attribute("data-group") == "routine"
        # Closed, with how many there are, and nothing of them on show.
        await expect(group).not_to_have_attribute("open", "")
        head = group.locator("summary.routine-head")
        await expect(head).to_have_attribute("data-count", "2")
        await expect(head.locator(".routine-title")).to_have_text("Routine moves")
        await expect(head.locator(".group-count")).to_have_text("2")
        await expect(head.locator(".routine-words")).to_have_count(0)
        await expect(group.locator("li.needs-row:visible")).to_have_count(0)
        await expect(page.locator("#needs-counts")).to_have_count(0)
        await expect(page.locator("#needs-reviewed")).to_have_text("Reviewed 0 of 3")

        # Opening it shows every entry as a whole card: its lines and its bar.
        await head.click()
        await expect(group).to_have_attribute("open", "")
        cards = group.locator("li.needs-row")
        await expect(cards).to_have_count(2)
        assert await cards.evaluate_all("els => els.map((e) => e.dataset.change)") == [
            f"placement:{n}" for n in routine
        ]
        for number in routine:
            card = card_of(page, f"placement:{number}")
            await expect(card).to_be_visible()
            await expect(card.locator(".diff-line[data-sign='+'][data-ref]")).to_have_count(1)
            await expect(card.locator(DECIDING)).to_have_text(["Approve", "Reject…"])
            # Grouping never hides a warning: a routine card has none.
            await expect(card.locator(".change-warning")).to_have_count(0)
            await expect(card.locator(".diff-flag")).to_have_count(0)
            assert await card.get_attribute("data-warning") is None
        # The score and the server's reason are on its review page.
        await open_review(page, f"placement:{routine[0]}")
        whole = page.locator("#review-page")
        await expect(whole.locator(".diff-line[data-sign='-'][data-ref]")).to_have_count(1)
        await expect(whole.locator(".diff-score")).to_have_count(1)
        await expect(whole.locator("p.change-routine-why")).to_contain_text("clearly best parent")
        await page.locator("#review-back").click()
        await on_page(page, "needs")
        await expect(page.locator("details.routine-group")).to_have_attribute("open", "")
        group = page.locator("details.routine-group")
        # Viewed counts them too.
        await card_of(page, f"placement:{routine[0]}").locator("input.change-viewed").check()
        await expect(page.locator("#needs-reviewed")).to_have_text("Reviewed 1 of 3")

        # The state of the group survives the page being drawn again.
        extra = await ok(
            agent,
            "create_node",
            actor="x",
            kind="task",
            title="Extra",
            parent=(
                await ok(
                    agent,
                    "create_node",
                    actor="x",
                    kind="story",
                    title="Holder",
                    parent=(
                        await ok(agent, "create_node", actor="x", kind="epic", title="Holder epic")
                    )["ref"],
                )
            )["ref"],
        )
        await ok(agent, "claim_node", node=extra["ref"])
        await ok(
            agent,
            "finish_work",
            node=extra["ref"],
            outcome="blocked",
            note="Need a person.",
            ask={"question": "Which one?", "choices": ["a", "b"]},
        )
        await expect(page.locator("li.needs-row[data-kind='ask']")).to_have_count(1, timeout=SLOW)
        await expect(group).to_have_attribute("open", "")
        await expect(
            card_of(page, f"placement:{routine[0]}").locator("input.change-viewed")
        ).to_be_checked()
        assert w.problems == []


async def test_the_rail_and_the_keys_reach_every_routine_move_open_or_closed() -> None:
    project = make_project(register())
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, routine, other = await three_proposals(agent)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(page.locator("details.routine-group")).to_have_count(1, timeout=SLOW)
        # The rail lists the group as one collapsed entry that opens the same way.
        rail = page.locator("nav#needs-rail")
        entry = rail.locator("details.rail-routine")
        await expect(entry).not_to_have_attribute("open", "")
        await expect(entry.locator("summary.rail-routine-head")).to_have_attribute(
            "data-count", "2"
        )
        await expect(entry.locator("summary .rail-group-title")).to_have_text("Routine moves")
        await expect(entry.locator("summary .group-count")).to_have_text("2")
        await entry.locator("summary").click()
        links = entry.locator("a.rail-change")
        await expect(links).to_have_count(2)
        assert await links.evaluate_all("els => els.map((e) => e.dataset.change)") == [
            f"placement:{n}" for n in routine
        ]
        # Choosing one opens the closed group on the page and puts the focus on the card.
        await links.last.click()
        await expect(page.locator("details.routine-group")).to_have_attribute("open", "")
        await expect(
            card_of(page, f"placement:{routine[1]}").locator(".change-head")
        ).to_be_focused()
        # The drop-down lists them too.
        options = page.locator(
            "select#needs-rail-select optgroup[label='Routine moves (2)'] option"
        )
        await expect(options).to_have_count(2)
        # Close it again: j and k stop on its summary, and o opens and closes it.
        await page.locator("details.routine-group summary").click()
        await expect(page.locator("details.routine-group")).not_to_have_attribute("open", "")
        await page.evaluate("document.activeElement && document.activeElement.blur()")
        order = [f"placement:{other[0]}"]
        await page.keyboard.press("j")
        assert (
            await page.evaluate("document.activeElement.closest('li.needs-row')?.dataset.change")
            == order[0]
        )
        await page.keyboard.press("j")
        focused = "document.activeElement.matches('summary.routine-head')"
        assert await page.evaluate(focused)
        await page.keyboard.press("j")
        assert await page.evaluate(focused), "the closed group is the last stop"
        await page.keyboard.press("o")
        await expect(page.locator("details.routine-group")).to_have_attribute("open", "")
        await page.keyboard.press("j")
        assert (
            await page.evaluate("document.activeElement.closest('li.needs-row')?.dataset.change")
            == f"placement:{routine[0]}"
        )
        await page.keyboard.press("j")
        await page.keyboard.press("k")
        await page.keyboard.press("k")
        assert await page.evaluate(focused)
        await page.keyboard.press("o")
        await expect(page.locator("details.routine-group")).not_to_have_attribute("open", "")
        # Nothing was decided by any of it.
        assert (await ok(agent, "list_placements", status="proposed"))["total"] == 3
        assert w.problems == []


# -- the split view --------------------------------------------------------------------------------


async def lines_of(card: Locator) -> Counter[tuple[str, str, str]]:
    """What a change adds and removes: the sign, the item and the kind of line of each line."""
    found = await card.locator(".diff-line[data-sign='+'], .diff-line[data-sign='-']").evaluate_all(
        "els => els.map((e) => [e.dataset.sign, e.dataset.ref ?? '', e.dataset.part ?? ''])"
    )
    return Counter(tuple(one) for one in found)


async def marks_of(card: Locator) -> dict[str, Counter[tuple[str, ...]]]:
    """What is drawn on a change besides its lines: flags, scores, folds and the lowest marker."""
    return {
        "flags": Counter(
            tuple(one)
            for one in await card.locator(".diff-flag").evaluate_all(
                "els => els.map((e) => [e.dataset.check, e.closest('.diff-line').dataset.ref])"
            )
        ),
        "scores": Counter(
            tuple(one)
            for one in await card.locator(".diff-score").evaluate_all(
                "els => els.map((e) => [e.dataset.ref, e.textContent])"
            )
        ),
        "folds": Counter(
            tuple(one)
            for one in await card.locator("button.diff-fold").evaluate_all(
                "els => els.map((e) => [e.dataset.fold])"
            )
        ),
        "lowest": Counter(
            tuple(one)
            for one in await card.locator(".diff-lowest").evaluate_all(
                "els => els.map((e) => [e.dataset.ref])"
            )
        ),
    }


async def test_split_and_unified_show_the_same_change_for_a_plan_and_for_a_move() -> None:
    project = make_project(register())
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        made = await plan_that_repeats_a_story(agent, refs["billing"], "Issue the invoice")
        mixed = await a_proposal(
            agent, [refs["loose_mail"], refs["loose_login"]], refs["billing"], "Both loose."
        )
        asked = await task_asks(agent, refs["billing1"])
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        # The list has no layout to choose: Unified and Split are on the review page.
        await expect(card_of(page, f"ask:{asked}")).to_be_visible(timeout=SLOW)
        await expect(page.locator("#needs-view, .diff-side")).to_have_count(0)

        # A plan: on its review page, the lines and the marks are the same in both layouts.
        await open_review(page, f"plan:{made['plan']}")
        plan = page.locator("#review-page")
        await expect(plan.locator("li.check-line").first).to_be_visible(timeout=SLOW)
        control = page.locator("#review-view")
        await expect(control.locator("button[aria-pressed='true']")).to_have_text("Unified")
        before = (await lines_of(plan), await marks_of(plan))
        assert before[0]
        assert "view=" not in await page.evaluate("location.hash")
        await control.get_by_role("button", name="Split").click()
        assert "view=split" in await page.evaluate("location.hash")
        await expect(control.locator("button[aria-pressed='true']")).to_have_text("Split")
        await expect(page.locator("#review-diff[data-view='split']")).to_have_count(1)
        assert (await lines_of(plan), await marks_of(plan)) == before
        await expect(plan.locator(".diff-side")).to_have_count(2)
        await expect(plan.locator(".diff-side-head")).to_have_text(["Before", "After"])
        old_side = plan.locator(".diff-side[data-side='old']")
        new_side = plan.locator(".diff-side[data-side='new']")
        assert await old_side.locator(".diff-line[data-sign='+']").count() == 0
        assert await new_side.locator(".diff-line[data-sign='-']").count() == 0
        # Before is only what exists, after is that and everything the plan adds.
        old_lines = old_side.locator(".diff-line")
        assert set(await old_lines.evaluate_all("els => els.map((e) => e.dataset.sign)")) <= {"."}
        context = await refs_of(new_side.locator(".diff-line[data-sign='.']"))
        assert await refs_of(old_lines) == context == [refs["billing"]]
        assert len(await item_lines(new_side).all()) == 4
        # The key s switches back and the address forgets it.
        await page.evaluate("document.activeElement && document.activeElement.blur()")
        await page.keyboard.press("s")
        await expect(page.locator("#review-diff[data-view='split']")).to_have_count(0)
        assert "view=" not in await page.evaluate("location.hash")
        assert (await lines_of(plan), await marks_of(plan)) == before
        await page.keyboard.press("s")
        await expect(page.locator("#review-diff[data-view='split']")).to_have_count(1)

        # A move: the old parent with the moved work removed, the new one with it added.
        await page.goto(f"{project.address}#/needs")
        await on_page(page, "needs")
        await open_review(page, f"placement:{mixed}")
        move = page.locator("#review-page")
        await expect(move.locator(".diff-score").first).to_be_visible(timeout=SLOW)
        await expect(page.locator("#review-diff")).to_have_attribute("data-view", "unified")
        before = (await lines_of(move), await marks_of(move))
        assert before[0]
        await page.locator("#review-view").get_by_role("button", name="Split").click()
        await expect(move.locator(".diff-side")).to_have_count(2)
        assert (await lines_of(move), await marks_of(move)) == before
        assert (
            await move.locator(".diff-side[data-side='old'] .diff-line[data-sign='+']").count() == 0
        )
        assert (
            await move.locator(".diff-side[data-side='new'] .diff-line[data-sign='-']").count() == 0
        )
        removed = await refs_of(
            move.locator(".diff-side[data-side='old'] .diff-line[data-sign='-']")
        )
        added = await refs_of(move.locator(".diff-side[data-side='new'] .diff-line[data-sign='+']"))
        assert sorted(removed) == sorted(added) == sorted([refs["loose_mail"], refs["loose_login"]])

        # A question has no before and no after, so its page draws no choice and no second column.
        await page.goto(f"{project.address}#/needs")
        await on_page(page, "needs")
        await open_review(page, f"ask:{asked}")
        await expect(page.locator("#review-view, .diff-side")).to_have_count(0)

        # An address that asks for it is honoured when the page loads, and nothing was decided.
        await page.goto(f"{project.address}#/review?change=placement:{mixed}&view=split")
        await expect(page.locator("#review-diff[data-view='split']")).to_have_count(1, timeout=SLOW)
        await expect(page.locator("#review-view button[aria-pressed='true']")).to_have_text("Split")
        assert (await ok(agent, "list_placements", status="proposed"))["total"] == 1
        assert (await ok(agent, "list_plans", status="proposed"))["total"] == 1
        assert w.problems == []


async def task_asks(agent: Client, story: str) -> int:
    task = await ok(agent, "create_node", actor="x", kind="task", title="Which one", parent=story)
    await ok(agent, "claim_node", node=task["ref"])
    await ok(
        agent,
        "finish_work",
        node=task["ref"],
        outcome="blocked",
        note="I need a person.",
        ask={"question": "Which one?", "choices": ["a", "b"]},
    )
    return int((await ok(agent, "get_node", node=task["ref"]))["asks"][0]["id"])


async def test_below_900_pixels_the_two_sides_stack_and_nothing_is_wider_than_the_window() -> None:
    project = make_project(register())
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        refs = await a_sorted_project(agent)
        made = await plan_that_repeats_a_story(agent, refs["billing"], "Issue the invoice")
        await a_proposal(agent, [refs["loose_mail"]], refs["billing"], "Mail.")
        page = w.page
        await page.goto(f"{project.address}#/review?change=plan:{made['plan']}&view=split")
        await expect(page.locator("#review-page li.check-line").first).to_be_visible(timeout=SLOW)
        await expect(page.locator("#review-diff[data-view='split']")).to_have_count(1)
        columns = (
            "getComputedStyle(document.querySelector('#review-diff .diff-sides'))"
            ".gridTemplateColumns.split(' ').length"
        )
        assert await page.evaluate(columns) == 2
        await no_overflow(page)
        # 899 stacks the two sides in one column, with the choice and the address as they were;
        # 900 has them side by side again.
        await page.set_viewport_size({"width": 899, "height": 900})
        await expect(page.locator("#review-diff[data-view='split']")).to_have_count(1)
        assert await page.evaluate(columns) == 1
        assert "view=split" in await page.evaluate("location.hash")
        await no_overflow(page)
        await page.set_viewport_size({"width": 900, "height": 900})
        assert await page.evaluate(columns) == 2
        await no_overflow(page)
        # At 390 it is one column too, and every part fits.
        await page.set_viewport_size({"width": 390, "height": 900})
        assert await page.evaluate(columns) == 1
        await no_overflow(page)
        assert w.problems == []


# -- confirming a note and ticking a check in place ----------------------------------------------


async def waiting_notes(agent: Client, project: Any) -> dict[str, Any]:
    """A task in progress with two unconfirmed notes, and another with one."""
    epic = await ok(agent, "create_node", actor="x", kind="epic", title="Checkout")
    story = await ok(agent, "create_node", actor="x", kind="story", title="Pay", parent=epic["ref"])
    first = await ok(
        agent, "create_node", actor="x", kind="task", title="Wire it", parent=story["ref"]
    )
    second = await ok(
        agent, "create_node", actor="x", kind="task", title="Test it", parent=story["ref"]
    )
    for task in (first, second):
        await ok(agent, "claim_node", node=task["ref"])
    notes = [
        await ok(
            agent,
            "add_note",
            text=f"The webhook retries for a day {n}\nThen it gives up.",
            why="Read it in the provider's docs",
            nodes=[first["ref"]],
        )
        for n in (1, 2)
    ]
    lone = await ok(
        agent, "add_note", text="Tests need a card", why="Seen in the log", nodes=[second["ref"]]
    )
    return {"first": first, "second": second, "notes": notes, "lone": lone}


async def test_a_note_is_confirmed_on_its_card_as_the_item_page_records_it() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        made = await waiting_notes(agent, project)
        n1, n2 = (n["id"] for n in made["notes"])
        lone = made["lone"]["id"]
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        card = card_of(page, f"note:{made['first']['ref']}")
        await expect(card.locator("li.preview-note")).to_have_count(2, timeout=SLOW)
        assert await card.locator("li.preview-note").evaluate_all(
            "els => els.map((e) => Number(e.dataset.note))"
        ) == [n1, n2]
        # The note is there to read, and each note has a bar of its own, with no bar for the row.
        await expect(card.locator(f"li.preview-note[data-note='{n1}'] .diff-subject")).to_have_text(
            "The webhook retries for a day 1"
        )
        await expect(card.locator(":scope > div.decision")).to_have_count(0)
        buttons = card.locator("li.preview-note button.verdict[data-verb=approve]")
        await expect(buttons).to_have_count(2)
        await expect(buttons.first).to_have_text("Approve")

        # The shared dialog opens empty, with the signed-in person's name.
        await buttons.first.click()
        box = dialog(page)
        await expect(box.locator("#confirm-person")).to_have_value("Priya Raman")
        await expect(box.locator("#confirm-quote")).to_have_value("")
        await box.locator("#confirm-quote").fill("I read it in the docs.")
        await box.get_by_role("button", name="Confirm", exact=True).click()
        await expect(box).to_have_count(0, timeout=SLOW)
        in_place = await ok(agent, "get_note", note_id=n1)
        assert (in_place["status"], in_place["confirmed_by"]) == ("confirmed", "Priya Raman")
        assert in_place["confirmed_at"]
        # The row had two notes: the card stays with the other one.
        await expect(card.locator("li.preview-note")).to_have_count(1, timeout=SLOW)
        assert await refs_of_notes(card) == [n2]

        # The same from the item's page: what the server records is the same.
        await open_item(w, f"note:{n2}", "needs")
        chip = page.locator("#item-page .chip.note-status")
        await choose_in_menu(page, chip, "confirm")
        box = dialog(page)
        await expect(box.locator("#confirm-person")).to_have_value("Priya Raman")
        await box.locator("#confirm-quote").fill("I read it in the docs.")
        await box.get_by_role("button", name="Confirm", exact=True).click()
        await expect(box).to_have_count(0, timeout=SLOW)
        from_page = await ok(agent, "get_note", note_id=n2)
        for field in ("status", "confirmed_by"):
            assert from_page[field] == in_place[field]
        assert from_page["confirmed_at"]
        for note_id in (n1, n2):
            events = (await ok(agent, "note_history", note_id=note_id))["items"]
            (event,) = [e for e in events if e["action"] == "note_confirmed"]
            assert (event["actor"], event["actor_kind"]) == ("Priya Raman", "person")
        await w.go("#/needs")
        await on_page(page, "needs")
        await expect(card_of(page, f"note:{made['first']['ref']}")).to_have_count(0, timeout=SLOW)

        # A note that was withdrawn meanwhile: the refusal is shown in full, in the server's words,
        # and nothing was confirmed.
        lone_card = card_of(page, f"note:{made['second']['ref']}")
        await expect(lone_card).to_be_visible(timeout=SLOW)
        await hold_back_news(page)
        await ok(agent, "withdraw_note", note_ids=[lone], reason="It was wrong.")
        await lone_card.locator("button.verdict[data-verb=approve]").click()
        box = dialog(page)
        await box.get_by_role("button", name="Confirm", exact=True).click()
        await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
        said = board_refusal(
            project,
            "set_confirmation",
            note_id=lone,
            confirmation="confirmed",
            person="Priya Raman",
        )
        plain(await refusal_in(page).inner_text(), said)
        await box.get_by_role("button", name="Cancel").click()
        assert (await ok(agent, "get_note", note_id=lone))["status"] == "withdrawn"
        assert w.problems == []


async def refs_of_notes(card: Locator) -> list[int]:
    found: list[int] = await card.locator("li.preview-note").evaluate_all(
        "els => els.map((e) => Number(e.dataset.note))"
    )
    return found


PERSON_CHECKS = "- [person] A person tried it on a phone\n- [person] A person read the copy\n"
LONG_HOW = "I tried it on two phones and a tablet, in a train. " * 6


def check_key(words: str) -> str:
    return hashlib.sha256(words.encode()).hexdigest()[:8]


async def finished_but_for_a_person(agent: Client, story: str, title: str) -> dict[str, Any]:
    task = await ok(agent, "create_node", actor="x", kind="task", title=title, parent=story)
    await ok(agent, "claim_node", node=task["ref"])
    await ok(
        agent,
        "finish_work",
        node=task["ref"],
        outcome="blocked",
        note="Everything is built. A person should look at it.",
    )
    return dict(task)


async def test_a_person_only_check_is_ticked_on_its_card_as_on_the_item_page() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        define(owner, project, PERSON_CHECKS)
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Checkout")
        story = await ok(
            agent, "create_node", actor="x", kind="story", title="Pay", parent=epic["ref"]
        )
        here = await finished_but_for_a_person(agent, story["ref"], "On the card")
        there = await finished_but_for_a_person(agent, story["ref"], "On the page")
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        card = card_of(page, f"check:{here['ref']}")
        lines = card.locator("li.preview-check")
        await expect(lines).to_have_count(2, timeout=SLOW)
        assert await lines.evaluate_all("els => els.map((e) => e.dataset.item)") == [
            check_key("A person tried it on a phone"),
            check_key("A person read the copy"),
        ]
        # Each check has a bar of its own, and no bar for the row.
        await expect(card.locator(":scope > div.decision")).to_have_count(0)
        await expect(
            card.locator("li.preview-check button.verdict[data-verb=approve]")
        ).to_have_count(2)

        # A refusal is shown in full, under the check, and nothing is ticked.
        first_line = lines.first
        await first_line.locator("button.decision-comment").click()
        field = first_line.locator("div.decision-comment-box textarea")
        await expect(field).to_be_focused()
        await field.fill(f"Used {A_TOKEN}")
        await first_line.locator("button.verdict[data-verb=approve]").click()
        problem = first_line.locator("p.decision-problem")
        await expect(problem).to_contain_text("looks like", timeout=SLOW)
        shown = (await problem.text_content()) or ""
        assert A_TOKEN not in shown
        said = as_person(
            owner,
            project,
            "set_check",
            node=here["ref"],
            item=check_key("A person tried it on a phone"),
            checked=True,
            evidence=f"Used {A_TOKEN}",
        )
        assert said.status == 400
        assert spoken(shown).endswith(spoken(words_of(said.body["message"]))) or spoken(
            words_of(said.body["message"])
        ) in spoken(shown)
        assert not [
            c for c in (await ok(agent, "get_node", node=here["ref"]))["checks"] if c["checked"]
        ]

        # Say how it was checked, in full, and approve: ticked, as the person, with the whole text.
        await field.fill(LONG_HOW.strip())
        await first_line.locator("button.verdict[data-verb=approve]").click()
        await expect(lines).to_have_count(1, timeout=SLOW)
        first = (await ok(agent, "get_node", node=here["ref"]))["checks"][0]
        assert (first["checked"], first["checked_by"], first["checked_by_kind"]) == (
            True,
            "Priya Raman",
            "person",
        )
        assert first["evidence"] == LONG_HOW.strip() and len(LONG_HOW) > 200
        # The evidence is read on the review page in full, with who ticked it.
        await open_review(page, f"check:{here['ref']}")
        ticked = page.locator("#review-page .review-check[data-checked='true']")
        await expect(ticked).to_contain_text(LONG_HOW.strip())
        await expect(ticked).to_contain_text("Priya Raman")
        await page.locator("#review-back").click()
        await on_page(page, "needs")
        # The second needs no words: the approval is the tick. The last open check takes the row
        # away.
        card = card_of(page, f"check:{here['ref']}")
        await card.locator("button.verdict[data-verb=approve]").click()
        await expect(card).to_have_count(0, timeout=SLOW)
        second = (await ok(agent, "get_node", node=here["ref"]))["checks"][1]
        assert (second["checked"], second["checked_by"], second["checked_by_kind"]) == (
            True,
            "Priya Raman",
            "person",
        )
        assert second["evidence"] is None

        # The item's page does the same, and the server records the same.
        await open_item(w, there["ref"], "needs")
        part = page.locator("#item-page [data-part='definition']")
        page_boxes = part.locator("input.check-box")
        await page_boxes.nth(0).check()
        await expect(part.locator(".check-who").first).to_be_visible(timeout=SLOW)
        await part.locator(".check-how").first.click()
        await part.locator(".check-how-field").fill(LONG_HOW.strip())
        await part.get_by_role("button", name="Save").click()
        await expect(part.locator(".check-evidence")).to_have_text(LONG_HOW.strip(), timeout=SLOW)
        await page_boxes.nth(1).check()
        await expect(part.locator(".check-who")).to_have_count(2, timeout=SLOW)
        checks_here = (await ok(agent, "get_node", node=here["ref"]))["checks"]
        checks_there = (await ok(agent, "get_node", node=there["ref"]))["checks"]

        def shape(checks: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
            return [
                (c["item"], c["checked"], c["checked_by"], c["checked_by_kind"]) for c in checks
            ]

        assert shape(checks_here) == shape(checks_there)
        assert w.problems == []


async def test_a_viewer_reads_every_part_of_a_review_and_has_no_control_that_writes() -> None:
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with connect(project.mcp_url, project.key) as agent:
        define(owner, project, PERSON_CHECKS)
        refs = await a_sorted_project(agent)
        made = await plan_that_repeats_a_story(agent, refs["billing"], "Issue the invoice")
        number = await a_proposal(agent, [refs["loose_pay"]], refs["billing"], "Invoices.")
        waiting = await waiting_notes(agent, project)
        story = (await ok(agent, "get_node", node=waiting["first"]["ref"]))["parent"]
        review = await finished_but_for_a_person(agent, story, "Needs a look")
        async with browser_page(project.address, token=None, person=viewer, name=None) as w:
            page = w.page
            await w.open("#/needs")
            await on_page(page, "needs")
            notes = card_of(page, f"note:{waiting['first']['ref']}")
            await expect(notes.locator("li.preview-note")).to_have_count(2, timeout=SLOW)
            await expect(notes.locator(".diff-subject").first).to_be_visible()
            checks = card_of(page, f"check:{review['ref']}")
            await expect(checks.locator("li.preview-check")).to_have_count(2, timeout=SLOW)
            # No control that writes: no bar, no verb, no comment, whatever the kind.
            await expect(
                page.locator("#needs-body .decision-bar, #needs-body button.decision-comment")
            ).to_have_count(0)
            await expect(page.locator(f"#needs-body li.needs-row {DECIDING}")).to_have_count(0)
            # Reading is theirs: the group, and the whole of every change on its review page.
            await expect(page.locator("details.routine-group summary")).to_have_count(1)
            await open_review(page, f"plan:{made['plan']}")
            await expect(page.locator("#review-page li.check-line").first).to_be_visible(
                timeout=SLOW
            )
            await expect(page.locator("#review-page .diff-flag").first).to_be_visible()
            await expect(page.locator("#review-view")).to_have_count(1)
            await page.locator("#review-back").click()
            await on_page(page, "needs")
            await open_routine(page)
            await open_review(page, f"placement:{number}")
            await expect(page.locator("#review-page .diff-score")).to_have_count(1, timeout=SLOW)
            await expect(
                page.locator("#review-bar, #review-page button.verdict, #review-page .diff-comment")
            ).to_have_count(0)
            assert w.problems == []
        # The server refuses a viewer the same calls, and an agent has no person-only permission.
        bad = viewer.call(
            "POST",
            f"/board/api/projects/{project.id}/tool/set_confirmation",
            {
                "note_id": waiting["notes"][0]["id"],
                "confirmation": "confirmed",
                "person": "Vera Viewer",
            },
        )
        assert bad.status in (400, 403), bad.body
        tick = as_person(
            viewer,
            project,
            "set_check",
            node=review["ref"],
            item=check_key("A person read the copy"),
            checked=True,
            evidence="Looked.",
        )
        assert tick.status in (400, 403), tick.body
        await refused(
            agent,
            "set_confirmation",
            note_id=waiting["notes"][0]["id"],
            confirmation="confirmed",
            person="an agent",
        )
        assert (await ok(agent, "get_note", note_id=waiting["notes"][0]["id"]))[
            "status"
        ] == "unconfirmed"
        assert not [
            c for c in (await ok(agent, "get_node", node=review["ref"]))["checks"] if c["checked"]
        ]


# -- the keyboard ----------------------------------------------------------------------------------


async def test_the_control_the_group_and_the_new_buttons_work_from_the_keyboard_alone() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        define(owner, project, PERSON_CHECKS)
        _, routine, _ = await three_proposals(agent)
        waiting = await waiting_notes(agent, project)
        story = (await ok(agent, "get_node", node=waiting["first"]["ref"]))["parent"]
        review = await finished_but_for_a_person(agent, story, "Needs a look")
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(page.locator("li.needs-row[data-kind='note']")).to_have_count(2, timeout=SLOW)
        await expect(page.locator("details.routine-group")).to_have_count(1)
        # The group opens with Enter on its summary, and its cards are reached with Tab.
        summary = page.locator("summary.routine-head")
        await tab_to(page, "summary.routine-head")
        await page.keyboard.press("Enter")
        await expect(page.locator("details.routine-group")).to_have_attribute("open", "")
        await page.keyboard.press("Enter")
        await expect(page.locator("details.routine-group")).not_to_have_attribute("open", "")
        await expect(summary).to_be_focused()
        # Approve is a button in the tab order; the key a goes to it and does not press it.
        notes = card_of(page, f"note:{waiting['first']['ref']}")
        await notes.locator(".change-head").focus()
        await page.keyboard.press("a")
        await expect(notes.locator("button.verdict[data-verb=approve]").first).to_be_focused()
        assert await page.locator("dialog[open]").count() == 0
        await page.keyboard.press("Enter")
        await expect(dialog(page)).to_be_visible()
        await expect(dialog(page).locator("#confirm-quote")).to_have_value("")
        await page.keyboard.press("Escape")
        await expect(dialog(page)).to_have_count(0)
        assert (await ok(agent, "get_note", note_id=waiting["notes"][0]["id"]))[
            "status"
        ] == "unconfirmed"
        # A check's Approve is in the tab order, a puts the focus on it and nothing is ticked.
        checks = card_of(page, f"check:{review['ref']}")
        await checks.locator(".change-head").focus()
        await page.keyboard.press("a")
        await expect(checks.locator("button.verdict[data-verb=approve]").first).to_be_focused()
        assert not [
            c for c in (await ok(agent, "get_node", node=review["ref"]))["checks"] if c["checked"]
        ]
        await page.keyboard.press("Enter")
        await expect(checks.locator("li.preview-check")).to_have_count(1, timeout=SLOW)
        ticked = [
            c for c in (await ok(agent, "get_node", node=review["ref"]))["checks"] if c["checked"]
        ]
        assert [c["checked_by"] for c in ticked] == ["Priya Raman"]
        # On the review page of a move the layout control is reached with Tab and pressed with
        # Enter.
        await page.locator("details.routine-group summary").click()
        await open_review(page, f"placement:{routine[0]}")
        await tab_to(page, "#review-view button[data-view='unified']")
        await page.keyboard.press("Tab")
        await expect(page.locator("#review-view button[data-view='split']")).to_be_focused()
        await page.keyboard.press("Enter")
        await expect(page.locator("#review-view button[aria-pressed='true']")).to_have_text("Split")
        assert "view=split" in await page.evaluate("location.hash")
        assert w.problems == []


# -- both themes and a narrow window -------------------------------------------------------------


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("screen", [SCREENS[0], SCREENS[2]], ids=lambda screen: screen.name)
async def test_every_new_chip_flag_and_head_reads_in_both_themes_at_390_and_1280(
    screen: Screen, theme: str
) -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        define(owner, project, PERSON_CHECKS)
        refs, _, _ = await three_proposals(agent)
        made = await plan_that_repeats_a_story(agent, refs["billing"], "Issue the invoice")
        plan_number = made["plan"]
        held = await a_proposal(agent, [refs["loose_story"]], refs["billing"], "Charges.")
        await ok(agent, "claim_node", node=refs["loose_story"])
        waiting = await waiting_notes(agent, project)
        story = (await ok(agent, "get_node", node=waiting["first"]["ref"]))["parent"]
        await finished_but_for_a_person(agent, story, "Needs a look")
        async with layout_context(project.address, screen, theme) as (w, context):
            scratch = await blank_page(context)
            page = w.page
            await page.goto(f"{project.address}#/needs")
            await expect(card_of(page, f"placement:{held}")).to_be_visible(timeout=SLOW)
            await expect(
                card_of(page, f"placement:{held}").locator(".change-warning")
            ).to_be_visible(timeout=SLOW)
            await page.locator("details.routine-group summary").click()
            await settle(page)
            for part in (
                ".change-warning",
                "summary.routine-head",
                "button.verdict[data-verb=approve]",
                ".change-kind",
            ):
                await expect(page.locator(f"#needs-body {part}").first).to_be_visible()
            found = await contrast(w, scratch)
            assert found.checked >= 50, found.checked
            assert found.found == [], found.lines()
            # The parts that only the review page draws: the checks and flags of a plan, the
            # scores of a move, and the two sides.
            for change, parts in (
                (f"plan:{plan_number}", (".change-checks li.check-line", ".diff-flag")),
                (
                    f"placement:{held}",
                    (".diff-score", ".diff-lowest, .diff-rank", ".diff-flag", ".diff-side-head"),
                ),
            ):
                await page.goto(f"{project.address}#/needs")
                await expect(page.locator("#needs-body li.needs-row").first).to_be_visible(
                    timeout=SLOW
                )
                await open_review(page, change)
                await page.locator("#review-view button[data-view=split]").click()
                await settle(page)
                for part in parts:
                    await expect(page.locator(f"#review-page {part}").first).to_be_visible()
                found = await contrast(w, scratch)
                assert found.found == [], (change, found.lines())
            assert w.problems == []


async def test_the_whole_review_page_with_every_part_open_fits_390_pixels() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        define(owner, project, PERSON_CHECKS)
        refs, routine, _ = await three_proposals(agent)
        made = await plan_that_repeats_a_story(agent, refs["billing"], "Issue the invoice")
        await waiting_notes(agent, project)
        page = w.page
        await page.set_viewport_size({"width": 390, "height": 900})
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(card_of(page, f"plan:{made['plan']}")).to_be_visible(timeout=SLOW)
        await page.locator("details.routine-group summary").click()
        await no_overflow(page)
        # Every kind of change, on its review page, with its parts and both layouts.
        for change in (f"plan:{made['plan']}", f"placement:{routine[0]}"):
            await page.goto(f"{project.address}#/needs")
            await expect(page.locator("#needs-body li.needs-row").first).to_be_visible(timeout=SLOW)
            await page.locator("details.routine-group summary").click()
            await open_review(page, change)
            await no_overflow(page)
            await page.locator("#review-view button[data-view=split]").click()
            await expect(page.locator("#review-diff .diff-side")).to_have_count(2)
            await no_overflow(page)
        await page.goto(f"{project.address}#/needs")
        await expect(card_of(page, f"plan:{made['plan']}")).to_be_visible(timeout=SLOW)
        await row(page, "plan", made["created"][0]["ref"]).locator(".change-head").focus()
        assert w.problems == []
