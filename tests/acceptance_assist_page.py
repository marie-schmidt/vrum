"""Browser proofs of review assistance (M50), in the chapter `assist`, as the pages are since M52:
what the review page shows of the checks of a plan and the scores of a suggested move, the routine
group on the list, and confirming a note and ticking a check with the bar. The proofs of what the
tools say are in tests/acceptance_assist.py, which this file builds on; each part works in a project
of its own, and a headless browser is signed in as the owner, an editor or a viewer of it."""

import math
from typing import Any

from playwright.async_api import Locator, Page, expect

from tests.acceptance_assist import (
    a_plan_that_needs_a_look,
    four_words,
    person,
    the_whole_plan,
)
from tests.acceptance_review import (
    ITEM_LINES,
    lines_of,
    open_review,
    plan_card,
    review_page,
    the_diff_is_before_the_decision,
)
from tests.acceptance_revising_page import failing, holds
from tests.acceptance_scenario import clip, proof
from tests.board_context import choose_in_menu, dialog, refusal_in
from tests.board_helpers import SLOW, Watched, browser_page, go, hold_back_news, part, plain
from tests.board_m14 import spoken
from tests.helpers import connect, embeddings_stopped, make_tree, ok, reindexed
from tests.test_checks_integration import define
from tests.test_placement_integration import a_sorted_project, node
from tests.world import join_project, make_project, register

PERSON_CHECK = "A person has tried it on a phone"
ANOTHER_CHECK = "A person has tried it with one hand"


async def the_needs_page(w: Watched, query: str = "") -> None:
    """Load the list afresh, with the choices after its address (such as "?group=topic")."""
    await go(w.page, w.board, f"#/needs{query}")


def percent(similarity: float) -> int:
    """Git's similarity index: a whole number, rounded down, never more than 100."""
    return min(100, max(0, math.floor(similarity * 100)))


def ordinal(number: int) -> str:
    tens = number % 100
    suffix = "th" if 10 <= tens <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def move_card(page: Page, number: int) -> Locator:
    return page.locator(f"li.needs-row[data-change='placement:{number}']")


async def flags_of(scope: Locator) -> dict[str, list[str]]:
    """Which checks flag which item's line in the diff: {reference: [check, ...]}."""
    found: list[list[Any]] = await scope.locator(
        ".change-diff .diff-line:has(.diff-flag)"
    ).evaluate_all(
        "els => els.map(e => [e.dataset.ref, [...e.querySelectorAll('.diff-flag')]"
        ".map(f => f.dataset.check)])"
    )
    flags: dict[str, list[str]] = {}
    for ref, checks in found:
        flags.setdefault(ref, []).extend(checks)
    return flags


# -- the checks of a plan ------------------------------------------------------------------------


async def the_checks_beside_the_plan() -> None:
    """The checks block of a plan, the flags beside the items they concern, the warnings fact, the
    rail's mark all say what the tool says, above the diff, with nothing sized."""
    owner = register()
    project = make_project(owner)
    repeats = four_words()
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, _ = await make_tree(agent)
        existing = await ok(
            agent, "create_node", actor="x", kind="story", title=repeats, parent=epic["ref"]
        )
        made = await a_plan_that_needs_a_look(agent, epic, repeats)
        await reindexed(agent)
        refs = {c["key"]: c["ref"] for c in made["created"]}
        plan = await the_whole_plan(agent, made["plan"])
    shown = [c for c in plan["checks"] if c["state"] != "ok"]
    change = f"plan:{made['plan']}"
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_needs_page(w)
        card = plan_card(page, made["plan"])
        listed = card.locator("[data-fact='warnings']")
        await expect(listed).to_be_visible(timeout=SLOW)
        on_the_card = spoken(await listed.inner_text())
        rail = page.locator(f"nav#needs-rail a.rail-change[data-change='{change}']")
        marked = await rail.locator(".rail-warning").count()
        await open_review(w, change)
        scope = review_page(page)
        block = scope.locator("section[data-part='checks']")
        await expect(block.locator("li.check-line")).to_have_count(len(shown), timeout=SLOW)
        lines = await block.locator("li.check-line").evaluate_all(
            "els => els.map(e => [e.dataset.check, e.dataset.state])"
        )
        said = spoken(await block.inner_text())
        findings = await block.locator(".check-finding").evaluate_all(
            "els => els.map(e => e.dataset.items)"
        )
        related = await block.locator(".check-related-item").evaluate_all(
            "els => els.map(e => [e.dataset.ref, Number(e.dataset.similarity)])"
        )
        ok_line = spoken(await block.locator("p.check-ok").inner_text())
        flags = await flags_of(scope)
        in_the_head = spoken(await scope.locator("[data-fact='warnings']").inner_text())
        before_the_diff = bool(
            await scope.evaluate(
                "el => !!(el.querySelector('section[data-part=checks]').compareDocumentPosition("
                "el.querySelector('.change-diff')) & Node.DOCUMENT_POSITION_FOLLOWING)"
            )
        )
        near = shown[1]["findings"][0]["related"][0]
        proof(
            "M50",
            "in a browser the checks of a plan are drawn above its diff on its review page, one "
            "line for each that is not ok, each with its state in words and the server's "
            "sentence, the findings naming the items they are about and, for repeated work, the "
            "existing story with how alike it is; every check that passed is one quiet "
            "sentence; a small flag sits on the line of each item a finding names (and on no "
            "other); the plan's warnings are a fact on its card and its page (2 warnings) and "
            "its rail entry is marked (the counted summary line is gone: replaced by M52)",
            lines == [[c["kind"], c["state"]] for c in shown]
            and all(c["words"] in said for c in shown)
            and sorted(findings)
            == sorted(" ".join(f["items"]) for c in shown for f in c["findings"])
            and related == [[near["ref"], near["similarity"]]]
            and near["ref"] == existing["ref"]
            and f"{round(near['similarity'] * 100)}% alike" in said
            and ok_line == "No circles"
            and flags
            == {
                refs["loose1"]: ["unsized"],
                refs["loose2"]: ["unsized"],
                refs["dup"]: ["duplicate"],
            }
            and "2" in in_the_head
            and "2" in on_the_card
            and before_the_diff
            and marked == 1,
            f"{[tuple(x) for x in lines]}, flags on {len(flags)} items; {on_the_card}",
        )

        # Nothing was sized by looking, and a warning holds nothing back: Approve works.
        sizes = [(await ok_node(project, ref))["size"] for ref in (refs["loose1"], refs["loose2"])]
        await scope.locator("button.verdict[data-verb='approve']").click()
        await expect(
            page.locator(f"#review-page[data-change='plan:{made['plan']}']")
        ).to_have_count(0, timeout=SLOW)
        after = await ok_plan(project, made["plan"])
        proof(
            "M50",
            "a plan whose checks warn is approved with the same button, by the signed-in person, "
            "as one decision; looking at the checks sized nothing and the decision changed "
            "nothing about what it does",
            sizes == [None, None]
            and after["state"] == "approved"
            and after["decided_by"] == owner.name
            and after["checks"] is None,
            f"approved by {owner.name}",
        )


async def ok_node(project: Any, ref: str) -> dict[str, Any]:
    async with connect(project.mcp_url, project.key) as agent:
        return dict(await ok(agent, "get_node", node=ref))


async def ok_plan(project: Any, number: int) -> dict[str, Any]:
    async with connect(project.mcp_url, project.key) as agent:
        return await the_whole_plan(agent, number)


async def the_check_that_could_not_be_made_is_visible() -> None:
    """With meaning search down the duplicate check is on the page as plainly as a warning, says
    it was not checked and never reads as no duplicates."""
    project = make_project(register())
    repeats = four_words()
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, _ = await make_tree(agent)
        await ok(agent, "create_node", actor="x", kind="story", title=repeats, parent=epic["ref"])
        await reindexed(agent)
        made = await a_plan_that_needs_a_look(agent, epic, repeats)
        await reindexed(agent)
    with embeddings_stopped():
        async with browser_page(project.address, token=project.key, name=None) as w:
            page = w.page
            await open_review(w, f"plan:{made['plan']}")
            scope = review_page(page)
            line = scope.locator("li.check-line[data-check='duplicate']")
            await expect(line).to_have_attribute("data-state", "unavailable", timeout=SLOW)
            said = spoken(await line.inner_text())
            ok_line = spoken(await scope.locator("p.check-ok").inner_text())
            head = spoken(await scope.locator("[data-fact='warnings']").inner_text())
            flagged = await flags_of(scope)
            proof(
                "M50",
                "with the embedding service stopped the check for repeated work is on the page "
                "as visible as a warning, in words that say it was not checked and why, it is "
                "counted in the plan's warnings, and the one quiet sentence for what passed does "
                "not claim that nothing repeats",
                "Not checked" in said
                and "cannot be reached" in said
                and "repeated" not in ok_line.lower()
                and "not checked" in head.lower()
                and "duplicate" not in {c for checks in flagged.values() for c in checks},
                clip(said, 90),
            )


async def a_plan_with_nothing_to_weigh() -> None:
    """A clean plan shows one quiet line and no warning anywhere."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic, _, _ = await make_tree(agent)
        made = await ok(
            agent,
            "create_plan",
            draft=True,
            title="All in order",
            items=[
                {
                    "key": "s",
                    "kind": "story",
                    "title": four_words(),
                    "parent": epic["ref"],
                    "size": "m",
                },
                {"key": "t", "kind": "task", "title": four_words(), "parent": "s", "size": "s"},
            ],
        )
        await reindexed(agent)
        plan = await the_whole_plan(agent, made["plan"])
    change = f"plan:{made['plan']}"
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, change)
        scope = review_page(page)
        await expect(scope.locator("p.check-ok")).to_be_visible(timeout=SLOW)
        ok_line = spoken(await scope.locator("p.check-ok").inner_text())
        ok_parts = await scope.locator("p.check-ok .check-ok-part").all_inner_texts()
        on_the_page = await scope.locator(
            "li.check-line, .diff-flag, [data-fact='warnings']"
        ).count()
        await the_needs_page(w)
        on_the_list = await page.locator(
            "[data-fact='warnings'], nav#needs-rail .rail-warning"
        ).count()
        proof(
            "M50",
            "a plan with nothing to weigh shows its checks as ONE quiet line of three short "
            "labels (no circles, every task sized, no repeated work) and nothing else: no line "
            "for a check, no "
            "flag, no warnings fact on its page or its card, none in the rail",
            all(c["state"] == "ok" for c in plan["checks"])
            and ok_parts == ["No circles", "Every task sized", "No repeated work"]
            and on_the_page == 0
            and on_the_list == 0,
            ok_line,
        )


# -- suggested moves -----------------------------------------------------------------------------


async def routine_moves_wait_in_a_group_of_their_own() -> None:
    """Scores on each moved item, the lowest marked, a warning that keeps a move out of the
    routine group, and a routine group that is closed, counted and opens to every one in full."""
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        extra = await node(agent, "story", "Charge the card payment", refs["inbox"])
        await reindexed(agent)
        made: dict[str, int] = {}
        for name, items, parent in (
            ("pay", [refs["loose_pay"]], refs["billing"]),
            ("mail", [refs["loose_mail"]], refs["mail"]),
            ("mixed", [refs["loose_login"], extra], refs["billing"]),
            ("held", [refs["loose_story"]], refs["billing"]),
        ):
            made[name] = (
                await ok(
                    agent,
                    "propose_placement",
                    items=items,
                    parent=parent,
                    reason=f"The {name} one.",
                )
            )["id"]
        await ok(agent, "claim_node", node=refs["loose_story"])
        waiting = {
            p["id"]: p for p in (await ok(agent, "list_placements", status="proposed"))["items"]
        }
    routine = sorted(n for n, p in waiting.items() if p["routine"])
    others = sorted(n for n, p in waiting.items() if not p["routine"])
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_needs_page(w)
        group = page.locator("section.needs-group[data-group='routine']")
        await expect(group).to_have_count(1, timeout=SLOW)
        last = await page.locator("section.needs-group").last.get_attribute("data-group")
        box = group.locator("details.routine-group")
        closed = await box.get_attribute("open") is None
        head = group.locator("summary.routine-head")
        title = spoken(await head.inner_text())
        count = await head.get_attribute("data-count")
        in_group = sorted(
            int(n)
            for n in await group.locator("li.needs-row").evaluate_all(
                "els => els.map(e => e.dataset.placement)"
            )
        )
        elsewhere = sorted(
            int(n)
            for n in await page.locator(
                "section.needs-group:not([data-group='routine']) "
                "li.needs-row[data-kind='placement']"
            ).evaluate_all("els => els.map(e => e.dataset.placement)")
        )
        hidden = [await move_card(page, n).is_visible() for n in routine]
        proof(
            "M50",
            "the proposals the server calls routine wait in ONE group after every other, closed, "
            "with how many there are, and everything else (the one with something to weigh, the "
            "one where an item ranks low) stays in its level's group, in full (the summary line "
            "that counted them is gone: replaced by M52)",
            routine == sorted([made["pay"], made["mail"]])
            and others == sorted([made["mixed"], made["held"]])
            and last == "routine"
            and closed
            and count == str(len(routine))
            and in_group == routine
            and elsewhere == others
            and not any(hidden),
            f"{title}; elsewhere {elsewhere}",
        )

        # Opening it shows every routine proposal as a card: what it moves, the bar, and on its
        # review page the scores and the server's words about why it is routine.
        await head.click()
        wrong: list[str] = []
        for number in routine:
            one = waiting[number]
            card = move_card(page, number)
            await expect(card).to_be_visible(timeout=SLOW)
            if await card.locator("div.decision-bar button.verdict").count() != 2:
                wrong.append(f"buttons of {number}")
            if (await card.locator("[data-fact='impact']").inner_text()).strip() != (
                f"Moves {len(one['items'])}"
            ):
                wrong.append(f"impact of {number}")
        for number in routine:
            one = waiting[number]
            await open_review(w, f"placement:{number}")
            scope = review_page(page)
            added = await lines_of(scope, ITEM_LINES, len(one["items"]))
            if added != [i["ref"] for i in one["items"]]:
                wrong.append(f"lines of {number}")
            for score in one["scores"]:
                chip = scope.locator(f".diff-score[data-ref='{score['ref']}']")
                if spoken(await chip.inner_text()) != f"{percent(score['similarity'])}%":
                    wrong.append(f"score of {score['ref']}")
            if (
                spoken(await scope.locator("p.change-routine-why").inner_text())
                != one["routine_why"]
            ):
                wrong.append(f"why of {number}")
            if not await the_diff_is_before_the_decision(scope):
                wrong.append(f"order of {number}")
        proof(
            "M50",
            "opening the routine group shows every routine proposal as a card with its bar, and "
            "its review page has the same added lines as the proposal, each moved item's "
            "similarity as a percent (rounded down) as suggest_parent gives it, the server's "
            "sentence about why it is routine, and the bar after the diff",
            not wrong and len(routine) == 2,
            f"{len(routine)} cards" + (f"; wrong: {wrong}" if wrong else ""),
        )

        # The others: scores, the lowest marked, where the parent ranks, and the warning.
        mixed = waiting[made["mixed"]]
        by_score = sorted(mixed["scores"], key=lambda s: s["similarity"])
        lowest = by_score[0]
        await open_review(w, f"placement:{made['mixed']}")
        scope = review_page(page)
        low_chip = scope.locator(f".diff-lowest[data-ref='{lowest['ref']}']")
        rank = scope.locator(f".diff-rank[data-ref='{lowest['ref']}']")
        await expect(low_chip).to_have_count(1, timeout=SLOW)
        lowest_marks = await scope.locator(".diff-lowest").count()
        rank_words = spoken(await rank.inner_text())
        await open_review(w, f"placement:{made['held']}")
        held = review_page(page)
        flag = held.locator(".diff-flag[data-check='warning']")
        await expect(flag).to_have_count(1, timeout=SLOW)
        flag_words = await flag.get_attribute("aria-label") or ""
        on_the_page = await held.locator("[data-fact='warnings']").count()
        await the_needs_page(w)
        # The proposals are read once for all of them: a card has no warnings until then.
        await expect(move_card(page, made["held"])).not_to_have_attribute(
            "data-reading", "true", timeout=SLOW
        )
        on_the_card = await move_card(page, made["held"]).locator("[data-fact='warnings']").count()
        proof(
            "M50",
            "a move that is not routine shows each item's similarity beside its line on its "
            "page, marks the lowest one, and says where the parent ranks when it is not first "
            "and who ranks first; a moved item held by an agent is flagged with the server's "
            "words, the move's warnings are a fact on its page and its card, and it stays out "
            "of the routine group (the status letter R is gone: replaced by M52)",
            holds(
                lowest["rank"] != 1,
                lowest_marks == 1,
                f"ranks {ordinal(lowest['rank'])}" in rank_words,
                "is held by" in flag_words,
                on_the_page == 1,
                on_the_card == 1,
                made["held"] in others,
            ),
            f"lowest {lowest['ref']} {percent(lowest['similarity'])}%, {rank_words!r}, "
            f"{flag_words!r}, {on_the_page}/{on_the_card}{failing()}",
        )

        # The keyboard: j and k stop on the group's summary, o opens and closes it, and the
        # summary opens with Enter.
        await the_needs_page(w)
        head = page.locator("section[data-group='routine'] summary.routine-head")
        for _ in range(12):
            await page.keyboard.press("j")
            if await head.evaluate("el => el === document.activeElement"):
                break
        stopped = await head.evaluate("el => el === document.activeElement")
        was_closed = not await page.locator("details.routine-group").evaluate("el => el.open")
        await page.keyboard.press("o")
        opened = await page.locator("details.routine-group").evaluate("el => el.open")
        await page.keyboard.press("o")
        closed_again = not await page.locator("details.routine-group").evaluate("el => el.open")
        await page.keyboard.press("Enter")
        by_enter = await page.locator("details.routine-group").evaluate("el => el.open")
        await page.keyboard.press("j")
        first_inside = await page.evaluate(
            "() => document.activeElement?.closest('li.needs-row')?.dataset.change ?? null"
        )
        decided = (await ok_placements(project))["total"]
        proof(
            "M50",
            "by keyboard alone j reaches the routine group's summary, o opens and closes it, "
            "Enter on the summary opens it, j then goes on into the first routine card, and "
            "no key decided anything: every proposal still waits",
            stopped
            and was_closed
            and opened
            and closed_again
            and by_enter
            and first_inside in {f"placement:{n}" for n in routine}
            and decided == len(waiting),
            f"{first_inside}",
        )


async def ok_placements(project: Any) -> dict[str, Any]:
    async with connect(project.mcp_url, project.key) as agent:
        return dict(await ok(agent, "list_placements", status="proposed"))


# -- the split view, now a choice of the review page ---------------------------------------------


async def the_layout_is_a_choice_of_the_page_in_its_address() -> None:
    """Unified and split read the same lines for a plan and for a move, the choice is in the
    address of the review page and never stored, and the key s toggles it (replaced by M52: the
    list's page-wide switch is gone; the proofs that each choice changes the lines are in the
    chapter standard)."""
    project = make_project(register())
    async with connect(project.mcp_url, project.key) as agent:
        refs = await a_sorted_project(agent)
        plan = await ok(
            agent,
            "create_plan",
            draft=True,
            title="Under billing",
            items=[
                {
                    "key": "s",
                    "kind": "story",
                    "title": four_words(),
                    "parent": refs["billing"],
                    "size": "m",
                    "body": "One line.\nTwo lines.",
                },
                {"key": "t", "kind": "task", "title": four_words(), "parent": "s", "size": "s"},
            ],
        )
        move = (
            await ok(
                agent,
                "propose_placement",
                items=[refs["loose_pay"], refs["loose_login"]],
                parent=refs["billing"],
                reason="Two for billing.",
            )
        )["id"]
        await reindexed(agent)
        tree = (await the_whole_plan(agent, plan["plan"]))["tree"]
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await open_review(w, f"plan:{plan['plan']}")
        scope = review_page(page)
        await lines_of(scope, ITEM_LINES, len(tree))
        before = await scope.locator(".change-diff").get_attribute("data-view")
        await scope.locator("#review-view button[data-view='split']").click()
        await expect(scope.locator(".change-diff[data-view='split']")).to_have_count(1)
        address = await page.evaluate("location.hash")
        old_side = scope.locator(".diff-side[data-side='old']")
        new_side = scope.locator(".diff-side[data-side='new']")
        old_context = await old_side.locator(".diff-line[data-sign='.']").evaluate_all(
            "els => els.map(e => e.dataset.ref)"
        )
        new_context = await new_side.locator(".diff-line[data-sign='.']").evaluate_all(
            "els => els.map(e => e.dataset.ref)"
        )
        added = await new_side.locator(
            ".diff-line[data-sign='+'][data-ref]:not([data-part])"
        ).evaluate_all("els => els.map(e => e.dataset.ref)")
        no_plus_before = await old_side.locator(".diff-line[data-sign='+']").count()
        await open_review(w, f"placement:{move}", view="split")
        scope = review_page(page)
        await expect(scope.locator(".change-diff[data-view='split']")).to_have_count(
            1, timeout=SLOW
        )
        old_move = scope.locator(".diff-side[data-side='old']")
        new_move = scope.locator(".diff-side[data-side='new']")
        removed_before = await old_move.locator(".diff-line[data-sign='-'][data-ref]").count()
        added_after = await new_move.locator(".diff-line[data-sign='+'][data-ref]").count()
        await page.evaluate("document.activeElement?.blur()")
        await page.keyboard.press("s")
        await expect(scope.locator(".change-diff[data-view='unified']")).to_have_count(
            1, timeout=SLOW
        )
        back = await scope.locator("#review-view button[data-view='unified']").get_attribute(
            "aria-pressed"
        )
        stored = await page.evaluate("Object.keys(localStorage)")
        proof(
            "M50",
            "the split view of a plan's review page has the Before side hold only what exists "
            "already (the epic it hangs under) and the After side the same and every item it "
            "adds; for a move Before has the moved items as - lines and After as + lines; "
            "Unified is the default, the choice is in the address of the page, a fresh load of "
            "an address with view=split is split, the key s toggles it back, and the choice is "
            "never kept in the browser (only the theme is); replaced by M52: the choice is the "
            "review page's, not the list's",
            before == "unified"
            and "view=split" in address
            and no_plus_before == 0
            and old_context == [refs["billing"]]
            and new_context == old_context
            and added == [one["ref"] for one in tree]
            and removed_before == 2
            and added_after == 2
            and back == "true"
            and set(stored) <= {"vrum.theme", "vrum.actor"},
            f"{address}; before-context {old_context}, added {len(added)} of {len(tree)}",
        )


# -- confirming a note and ticking a check with the bar --------------------------------------------


async def what_waits_for_a_person(
    project: Any, owner: Any, *, two_checks: bool = False
) -> dict[str, Any]:
    """Notes on work an agent is doing (two on one task, one on another) and work finished but for
    a check only a person can tick (two tasks, each with that check, or with two of them)."""
    more = f"- [person] {ANOTHER_CHECK}\n" if two_checks else ""
    define(owner, project, f"- [person] {PERSON_CHECK}\n{more}")
    async with connect(project.mcp_url, project.key) as agent:
        epic = await ok(agent, "create_node", actor="x", kind="epic", title="Checkout")
        story = await ok(
            agent, "create_node", actor="x", kind="story", title="Pay", parent=epic["ref"]
        )
        tasks: dict[str, dict[str, Any]] = {}
        for name in ("noted", "noted2", "ticked", "ticked2"):
            tasks[name] = await ok(
                agent,
                "create_node",
                actor="x",
                kind="task",
                title=f"Task {name} {four_words()}",
                parent=story["ref"],
            )
            await ok(agent, "claim_node", node=tasks[name]["ref"])
        notes = {
            "first": await ok(
                agent,
                "add_note",
                text="The webhook retries for a day",
                why="Read it in the provider's docs",
                nodes=[tasks["noted"]["ref"]],
            ),
            "second": await ok(
                agent,
                "add_note",
                text="The refund window is 14 days",
                why="It is on the pricing page",
                nodes=[tasks["noted"]["ref"]],
            ),
            "other": await ok(
                agent,
                "add_note",
                text="Cards are never stored",
                why="Only the provider holds them",
                nodes=[tasks["noted2"]["ref"]],
            ),
        }
        for name in ("ticked", "ticked2"):
            await ok(
                agent,
                "finish_work",
                node=tasks[name]["ref"],
                outcome="blocked",
                note="Everything is built. A person should look at it on a phone.",
            )
        checks = (await ok(agent, "get_node", node=tasks["ticked"]["ref"]))["checks"]
        item = checks[0]["item"]
    return {"tasks": tasks, "notes": notes, "item": item, "items": [c["item"] for c in checks]}


async def confirming_a_note_with_the_bar() -> None:
    """Confirming with the bar records the signed-in person as the item page does, a row of
    several notes has a bar for each, and a refusal is the server's own words."""
    owner = register()
    project = make_project(owner)
    waiting = await what_waits_for_a_person(project, owner)
    tasks, notes = waiting["tasks"], waiting["notes"]
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_needs_page(w)
        row = page.locator(f"li.needs-row[data-kind='note'][data-node='{tasks['noted']['ref']}']")
        await expect(row).to_have_count(1, timeout=SLOW)
        bars = row.locator("div.decision-bar")
        await expect(bars).to_have_count(2, timeout=SLOW)
        whole = [notes["first"]["text"], notes["second"]["text"]]
        for piece in whole:
            await expect(row).to_contain_text(piece, timeout=SLOW)
        text = spoken(await row.inner_text())
        proof(
            "M50",
            "a row that stands for two unconfirmed notes lists both, each with its text and a "
            "bar of its own to approve or reject it (the confirm buttons inside the row are the "
            "bars: replaced by M52)",
            await row.locator("button.verdict[data-verb='approve']").count() == 2
            and await row.locator("button.verdict[data-verb='reject']").count() == 2
            and all(piece in text for piece in whole),
            f"{await bars.count()} bars",
        )

        # Approve the first from its bar: the dialog opens empty but for the person's name.
        await row.locator(f"button.verdict[aria-label='Approve N-{notes['first']['id']}']").click()
        box = dialog(page)
        await expect(box.locator("#confirm-person")).to_have_value(owner.name, timeout=SLOW)
        quote_was_empty = await box.locator("#confirm-quote").input_value() == ""
        await box.locator("#confirm-quote").fill("I read it in the docs too")
        await box.get_by_role("button", name="Confirm", exact=True).click()
        await expect(box).to_have_count(0, timeout=SLOW)
        await expect(bars).to_have_count(1, timeout=SLOW)
        async with connect(project.mcp_url, project.key) as agent:
            from_the_card = await ok(agent, "get_note", note_id=notes["first"]["id"])
            card_history = (await ok(agent, "note_history", note_id=notes["first"]["id"]))["items"]

        # The item page, for the other note: the same dialog and the same record.
        await w.open(f"#/item?item=note:{notes['other']['id']}&from=lists")
        chip = page.locator("#item-page .card-summary dd.status .chip.note-status")
        await expect(chip).to_have_attribute("data-status", "unconfirmed", timeout=SLOW)
        await choose_in_menu(page, chip, "confirm")
        item_box = dialog(page)
        await item_box.locator("#confirm-quote").fill("I read it in the docs too")
        await item_box.get_by_role("button", name="Confirm", exact=True).click()
        await expect(chip).to_have_attribute("data-status", "confirmed", timeout=SLOW)
        async with connect(project.mcp_url, project.key) as agent:
            from_the_page = await ok(agent, "get_note", note_id=notes["other"]["id"])
            page_history = (await ok(agent, "note_history", note_id=notes["other"]["id"]))["items"]

        def recorded(found: dict[str, Any], history: list[dict[str, Any]]) -> tuple[Any, ...]:
            last = history[-1]
            return (
                found["status"],
                found["confirmed_by"],
                bool(found["confirmed_at"]),
                last["action"],
                last["actor"],
                last["actor_kind"],
                last["after"].get("quote"),
            )

        proof(
            "M50",
            "approving a note with its bar opens the item page's own dialog, empty but for the "
            "signed-in person's name, and records what the item page records: the note is "
            "confirmed by that person, with the words they gave, one note_confirmed event by "
            "that person as a person; the note leaves the row and the other stays",
            quote_was_empty
            and recorded(from_the_card, card_history) == recorded(from_the_page, page_history)
            and from_the_card["confirmed_by"] == owner.name
            and recorded(from_the_card, card_history)[3:7]
            == ("note_confirmed", owner.name, "person", "I read it in the docs too"),
            str(recorded(from_the_card, card_history)),
        )

        # A refusal: the note was withdrawn after the page last heard of it, so confirming it
        # now is refused, and the dialog says what the board's own call says, in full.
        await the_needs_page(w)
        row = page.locator(f"li.needs-row[data-kind='note'][data-node='{tasks['noted']['ref']}']")
        await expect(row.locator("div.decision-bar")).to_have_count(1, timeout=SLOW)
        await hold_back_news(page)
        async with connect(project.mcp_url, project.key) as agent:
            await ok(
                agent, "withdraw_note", note_ids=[notes["second"]["id"]], reason="Not true any more"
            )
        await row.locator("button.verdict[data-verb='approve']").click()
        box = dialog(page)
        await box.get_by_role("button", name="Confirm", exact=True).click()
        await expect(refusal_in(page)).to_be_visible(timeout=SLOW)
        said = spoken(await refusal_in(page).inner_text())
        tried = person(
            project,
            owner,
            "set_confirmation",
            note_id=notes["second"]["id"],
            confirmation="confirmed",
            person=owner.name,
        )
        async with connect(project.mcp_url, project.key) as agent:
            still = await ok(agent, "get_note", note_id=notes["second"]["id"])
        plain(said, tried.body["message"])
        proof(
            "M50",
            "a confirmation the server refuses (the note was withdrawn after the page last "
            "heard of it) shows the server's own words in the dialog, in full, and changes "
            "nothing: the note is still withdrawn and nobody is recorded as confirming it",
            tried.status == 400
            and still["status"] == "withdrawn"
            and still["confirmed_by"] is None,
            clip(said, 100),
        )


async def ticking_a_check_with_the_bar() -> None:
    """Ticking a person-only check with the bar equals the item page, evidence and refusal in
    full, and ticking the last open check takes the row away."""
    owner = register()
    project = make_project(owner)
    waiting = await what_waits_for_a_person(project, owner)
    tasks, item = waiting["tasks"], waiting["item"]
    how = "Tried it on a phone: the form works"
    secret = "ghp_" + "a" * 36
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_needs_page(w)
        row = page.locator(f"li.needs-row[data-kind='check'][data-node='{tasks['ticked']['ref']}']")
        await expect(row).to_have_count(1, timeout=SLOW)
        bar = row.locator("div.decision").first
        await expect(row).to_contain_text(PERSON_CHECK, timeout=SLOW)
        text = spoken(await row.inner_text())
        # Evidence that looks like a secret is refused, in words, beside the bar, and nothing is
        # ticked; the words typed stay.
        await bar.locator("button.decision-comment").click()
        field = bar.locator("div.decision-comment-box textarea")
        await field.fill(secret)
        await bar.locator("button.verdict[data-verb='approve']").click()
        problem = bar.locator("p.problem")
        await expect(problem).to_contain_text("looks like", timeout=SLOW)
        refusal = spoken(await problem.inner_text())
        async with connect(project.mcp_url, project.key) as agent:
            untouched = (await ok(agent, "get_node", node=tasks["ticked"]["ref"]))["checks"][0]
        by_the_board = person(
            project,
            owner,
            "set_check",
            node=tasks["ticked"]["ref"],
            item=item,
            checked=True,
            evidence=secret,
        )
        plain(refusal, by_the_board.body["message"])
        proof(
            "M50",
            "a check only a person can tick shows its words and an Approve button, and a tick "
            "the server refuses (evidence that looks like a secret) shows the server's own "
            "words beside the bar, in full, and ticks nothing; the words typed stay",
            PERSON_CHECK in text
            and by_the_board.status == 400
            and untouched["checked"] is False
            and await field.input_value() == secret,
            clip(refusal, 90),
        )

        # Tick it with how it was checked.
        await field.fill(how)
        await bar.locator("button.verdict[data-verb='approve']").click()
        await expect(row).to_have_count(0, timeout=SLOW)
        async with connect(project.mcp_url, project.key) as agent:
            from_the_card = (await ok(agent, "get_node", node=tasks["ticked"]["ref"]))["checks"][0]

        # The same, from the item page, for the other task.
        await w.open(f"#/item?item={tasks['ticked2']['ref']}&from=board")
        definition = part(page, "definition")
        line = definition.locator(f"li.check-item[data-item='{item}']")
        await expect(line).to_be_visible(timeout=SLOW)
        await line.locator(".check-how").click()
        await definition.locator(".check-how-field").fill(how)
        await definition.locator("form.check-form button[type=submit]").click()
        await expect(line).to_have_attribute("data-checked", "true", timeout=SLOW)
        async with connect(project.mcp_url, project.key) as agent:
            from_the_page = (await ok(agent, "get_node", node=tasks["ticked2"]["ref"]))["checks"][0]
            history = (await ok(agent, "node_history", node=tasks["ticked"]["ref"]))["items"]
        keys = ("item", "text", "checked", "checked_by", "checked_by_kind", "evidence")
        proof(
            "M50",
            "ticking a person-only check with its bar, with how it was checked in the comment, "
            "records what the item page records: ticked, by the signed-in person as a person, "
            "with the evidence in full (read back by get_node), one check_ticked event, and, as "
            "it was the last open check, the row is gone from the list",
            all(from_the_card[k] == from_the_page[k] for k in keys)
            and from_the_card["checked"] is True
            and from_the_card["checked_by"] == owner.name
            and from_the_card["checked_by_kind"] == "person"
            and from_the_card["evidence"] == how
            and [e["action"] for e in history if e["action"].startswith("check_")]
            == ["check_ticked"],
            f"{from_the_card['checked_by']}: {from_the_card['evidence']}",
        )

        # Approve alone, with no words: ticked at once.
        async with connect(project.mcp_url, project.key) as agent:
            third = await ok(
                agent,
                "create_node",
                actor="x",
                kind="task",
                title=four_words(),
                parent=tasks["ticked"]["parent"],
            )
            await ok(agent, "claim_node", node=third["ref"])
            await ok(
                agent,
                "finish_work",
                node=third["ref"],
                outcome="blocked",
                note="Done but for the check.",
            )
        await the_needs_page(w)
        again = page.locator(f"li.needs-row[data-kind='check'][data-node='{third['ref']}']")
        await again.locator("button.verdict[data-verb='approve']").click()
        await expect(again).to_have_count(0, timeout=SLOW)
        async with connect(project.mcp_url, project.key) as agent:
            quick = (await ok(agent, "get_node", node=third["ref"]))["checks"][0]
        proof(
            "M50",
            "pressing Approve alone ticks the check at once as the signed-in person, as on the "
            "item page, and the row is gone",
            quick["checked"] is True
            and quick["checked_by"] == owner.name
            and quick["evidence"] is None,
            quick["checked_by"],
        )


async def a_viewer_cannot_write_here() -> None:
    """A viewer sees every note and every check with its evidence in full and no control that
    writes, and the server refuses the same calls."""
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    waiting = await what_waits_for_a_person(project, owner, two_checks=True)
    tasks, notes, items = waiting["tasks"], waiting["notes"], waiting["items"]
    how = "Looked at it on a phone"
    # One of the two checks is ticked, with how it was checked, so that the row still waits for the
    # other and the viewer can read the evidence in full.
    ticked = person(
        project,
        owner,
        "set_check",
        node=tasks["ticked"]["ref"],
        item=items[0],
        checked=True,
        evidence=how,
    )
    async with browser_page(project.address, token=None, person=viewer, name=None) as w:
        page = w.page
        await open_review(w, f"note:{tasks['noted']['ref']}")
        await expect(review_page(page)).to_contain_text(notes["second"]["text"], timeout=SLOW)
        text = spoken(await review_page(page).inner_text())
        await open_review(w, f"check:{tasks['ticked']['ref']}")
        await expect(review_page(page)).to_contain_text(how, timeout=SLOW)
        await expect(review_page(page)).to_contain_text(ANOTHER_CHECK, timeout=SLOW)
        check_text = spoken(await review_page(page).inner_text())
        await the_needs_page(w)
        writes = await page.locator(
            "button.verdict, button.decision-comment, .change-confirm, input.change-tick, "
            ".ask-choice"
        ).count()
        tried = [
            person(
                project,
                viewer,
                "set_confirmation",
                note_id=notes["first"]["id"],
                confirmation="confirmed",
                person=viewer.name,
            ),
            person(
                project,
                viewer,
                "set_check",
                node=tasks["ticked"]["ref"],
                item=items[1],
                checked=True,
                evidence="x",
            ),
        ]
        proof(
            "M50",
            "a viewer sees every unconfirmed note whole, and the checks of a task with the "
            "words of what was ticked and how it was checked in full and the one still open, "
            "with no button to approve, no comment control and no deciding button on the list, "
            "and the server refuses a viewer both calls",
            ticked.status == 200
            and notes["first"]["text"] in text
            and notes["second"]["text"] in text
            and PERSON_CHECK in check_text
            and ANOTHER_CHECK in check_text
            and how in check_text
            and writes == 0
            and all(t.status in (400, 403) for t in tried),
            f"{writes} controls, refused {[t.status for t in tried]}",
        )


async def assist_on_the_page() -> None:
    await the_checks_beside_the_plan()
    await the_check_that_could_not_be_made_is_visible()
    await a_plan_with_nothing_to_weigh()
    await routine_moves_wait_in_a_group_of_their_own()
    await the_layout_is_a_choice_of_the_page_in_its_address()
    await confirming_a_note_with_the_bar()
    await ticking_a_check_with_the_bar()
    await a_viewer_cannot_write_here()
