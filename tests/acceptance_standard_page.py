"""Browser proofs of the M52 chapter (tests/acceptance_standard.py): the Needs a person list as a
triage inbox of compact changes, and the full-screen review page every change opens. Each proof
compares what the page shows with what a real MCP client reads from the tools (`needs_a_person`,
`list_plans`, `list_placements`, `get_node`, `get_note`), in a project of its own, with a headless
browser signed in as the owner or a viewer of it. The proofs of what the server says about each
row (its level, decision and topic) are in tests/acceptance_standard.py, which this file builds on;
what a decision records is in tests/acceptance_standard_decide.py."""

import re
from typing import Any

from playwright.async_api import Locator, Page, expect

from tests.acceptance_review import ITEM_LINES, open_review
from tests.acceptance_scenario import proof
from tests.acceptance_standard import every_kind, make, rows_of
from tests.board_helpers import SLOW, Watched, browser_page, go
from tests.board_m14 import spoken
from tests.helpers import connect, ok, reindexed
from tests.world import join_project, make_project, register

WIDE = {"width": 1280, "height": 1000}
NARROW = {"width": 390, "height": 900}
MODES = ("level", "topic", "decision")

# Every text node of six or more words that is on the list, in its rail, on the review page or in
# an open dialog and is not somebody's own words, the server's refusal or a hover card: the page
# says nothing in sentences about what a control does.
SENTENCES = """() => {
    const skip = '[data-words="theirs"], .problem, [role=alert], .hovercard';
    const roots = ['#needs-body', '#needs-rail', '#review-page', 'dialog[open]']
        .flatMap(selector => [...document.querySelectorAll(selector)]);
    const found = [];
    for (const root of roots) {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const text = node.textContent.replace(/\\s+/g, ' ').trim();
            if (text && text.split(' ').length >= 6 && !node.parentElement.closest(skip)) {
                found.push(text);
            }
        }
    }
    return found;
}"""

# The groups of the list: [what data-group says, its title, its count, the changes in it].
GROUPS = """els => els.map(g => [
    g.dataset.group,
    g.querySelector('.group-title')?.textContent.trim() ?? '',
    g.querySelector('.group-count')?.textContent.trim() ?? '',
    [...g.querySelectorAll('li.needs-row')].map(r => r.dataset.change),
])"""


def change_id(row: dict[str, Any]) -> str:
    """What the page calls a change, which is also its address on the review page."""
    kind = row["kind"]
    if kind == "plan":
        return f"plan:{row['plan']['id']}"
    if kind == "placement":
        return f"placement:{row['placement']['id']}"
    if kind == "ask":
        return f"ask:{row['ask']['id']}"
    if kind == "gate":
        return f"gate:{row['gate']['id']}"
    return f"{kind}:{row['node']}"


def card(page: Page, change: str) -> Locator:
    return page.locator(f"li.needs-row[data-change='{change}']")


def review(page: Page) -> Locator:
    return page.locator("#review-page")


async def sentences(page: Page) -> list[str]:
    found: list[str] = await page.evaluate(SENTENCES)
    return found


class Seen:
    """What the tools say about a project's list: the rows, which suggested moves are routine and
    which plans wait for their agent."""

    def __init__(self, needs: dict[str, Any], moves: list[dict[str, Any]], later: list[int]):
        self.needs = needs
        self.rows = rows_of(needs)
        self.routine = {f"placement:{m['id']}" for m in moves if m["routine"]}
        self.later = later

    @property
    def total(self) -> int:
        return int(self.needs["total"])

    def key(self, row: dict[str, Any], mode: str) -> str:
        if mode == "level":
            return str(row["level"])
        if mode == "decision":
            return str(row["decision"])
        return str(row["topic"]["id"]) if row["topic"] else "other"

    def groups(self, mode: str) -> dict[str, list[str]]:
        """The groups of one way of grouping, as the server's fields make them: every row that is
        not a routine move, in the server's order."""
        found: dict[str, list[str]] = {}
        for row in self.rows:
            if change_id(row) not in self.routine:
                found.setdefault(self.key(row, mode), []).append(change_id(row))
        return found

    def order(self, mode: str) -> list[str]:
        """The groups in the order they are shown: levels, decisions in their own order, topics by
        their longest wait (the oldest row first) and the others last."""
        found = self.groups(mode)
        if mode == "level":
            return [k for k in ("plan", "epic", "story", "task", "note") if k in found]
        if mode == "decision":
            return [k for k in ("approve", "answer", "look_into") if k in found]
        waited = {
            key: min(row["waiting_since"] for row in self.rows if change_id(row) in found[key])
            for key in found
            if key != "other"
        }
        return sorted(waited, key=lambda key: waited[key]) + (["other"] if "other" in found else [])


async def seen_by_the_tools(agent: Any, *, later: list[int]) -> Seen:
    needs = await ok(agent, "needs_a_person")
    moves = (await ok(agent, "list_placements", status="proposed", limit=50))["items"]
    return Seen(needs, moves, later)


async def the_list(w: Watched, seen: Seen, mode: str = "level", **more: str) -> None:
    """Load the list afresh (with the way of grouping in its address, as a bookmark has it) and wait
    until every change is drawn and the proposals have been read, so that a routine move is in its
    group."""
    query = "&".join(
        [*(f"group={mode}" for _ in [0] if mode != "level"), *(f"{k}={v}" for k, v in more.items())]
    )
    await go(w.page, w.board, "#/needs" + (f"?{query}" if query else ""))
    page = w.page
    await expect(page.locator("li.needs-row")).to_have_count(seen.total, timeout=SLOW)
    await expect(page.locator("section.needs-group[data-group='routine']")).to_have_count(
        1 if seen.routine else 0, timeout=SLOW
    )
    await page.wait_for_function(
        "() => !document.querySelector('li.needs-row[data-reading]')", timeout=SLOW
    )


async def open_the_routine_group(page: Page) -> None:
    group = page.locator("details.routine-group")
    if await group.count() and not await group.evaluate("el => el.open"):
        await group.locator("summary").click()


async def the_review_page(w: Watched, change: str, **more: str) -> None:
    """Open the review page of a change by its address, as a link to it does, and wait until it is
    drawn."""
    await open_review(w, change, **more)
    await expect(review(w.page)).to_be_visible(timeout=SLOW)


async def a_question(agent: Any, title: str, **shape: Any) -> tuple[dict[str, Any], int]:
    """A task with a question put to a person: `choices`, `approval` or nothing (free words)."""
    epic = await make(agent, "epic", f"Epic of {title}")
    story = await make(agent, "story", f"Story of {title}", epic["ref"])
    task = await make(agent, "task", title, story["ref"])
    await ok(agent, "claim_node", node=task["ref"])
    await ok(
        agent,
        "finish_work",
        node=task["ref"],
        outcome="blocked",
        note="I need a decision.",
        ask={"question": f"{title}?", **shape},
    )
    asked = (await ok(agent, "get_node", node=task["ref"]))["asks"][0]["id"]
    return task, int(asked)


async def a_busy_project() -> Any:
    """The nine rows of acceptance_standard.every_kind and a yes or no question and a free one
    (eleven rows), an index that has caught up, a viewer, and what the tools say of them."""
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer")
    async with connect(project.mcp_url, project.key) as agent:
        made = await every_kind(owner, project, agent)
        made["approval"] = (await a_question(agent, "Drop the old format", approval=True))[1]
        made["free"] = (await a_question(agent, "Name the release"))[1]
        await reindexed(agent)
        seen = await seen_by_the_tools(agent, later=[made["later"]])
    return owner, project, viewer, made, seen


# -- the list ------------------------------------------------------------------------------------


async def the_list_has_no_letters_counts_or_switches() -> None:
    """The list is a triage inbox: a progress bar, a Group by choice and the keys, and no letter,
    diffstat, counted sentence, sentence about what a button does, layout switch or plan waiting
    line; the key s does nothing."""
    _, project, _, made, seen = await a_busy_project()
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_list(w, seen)
        gone = [
            "#needs-counts",
            "#needs-view",
            ".change-letter",
            ".diffstat",
            "p.change-consequence",
            "p.change-unsent-line",
            "p#needs-waiting-agent",
            "#plan-page",
            ".segmented",
            "#review-mode",
            "#review-view",
            "button[data-view]",
            "button[data-mode]",
        ]
        left = [s for s in gone if await page.locator(s).count()]
        wanted = [
            "#needs-reviewed",
            "select#needs-group",
            "#needs-keys-button",
            "nav#needs-rail",
        ]
        missing = [s for s in wanted if not await page.locator(s).count()]
        head = spoken(await page.locator("#needs-overview").inner_text())
        before = await page.locator("li.needs-row").evaluate_all(
            "els => els.map(e => e.dataset.change)"
        )
        await page.evaluate("document.activeElement?.blur()")
        await page.keyboard.press("s")
        after = await page.locator("li.needs-row").evaluate_all(
            "els => els.map(e => e.dataset.change)"
        )
        address = await page.evaluate("location.hash")
        proof(
            "M52",
            "the list has no status letter, no five-square diffstat, no counted sentence "
            "(nothing like '9 changes: 2 to add'), no sentence under a change about what its "
            "buttons do, no Unified or Split switch (and no diff, so nothing a switch could "
            "leave unchanged), no Changes or Whole plan switch and no line about a plan that "
            "waits for its agent; it has the progress count, the Group by choice, the Keys "
            "button and the rail, and the key s changes nothing",
            not left
            and not missing
            and re.search(r"\d+ changes?:", head) is None
            and "to add" not in head
            and "Reviewed" in head
            and before == after
            and "view=" not in address,
            f"{head!r}" + (f"; still there: {left}" if left else ""),
        )


PAGE_TEXT = """() => ['#needs-body', '#needs-rail', '#review-page', 'dialog[open]']
    .flatMap(s => [...document.querySelectorAll(s)])
    .map(e => e.innerText).join('\\n')"""
# A counted head line ("Plan 9: 7 to add", "Proposal 1365: 2 to move") and the hunk mark of a
# diff are not on any page: the numbers live in the facts, and a hunk row is plain words.
COUNTED_HEAD = re.compile(r"\b(Plan|Proposal)\s+\d+\s*:|\b\d+\s+to\s+(add|move)\b", re.IGNORECASE)


async def titles_of(scope: Locator) -> list[tuple[str, list[str]]]:
    """The title of each change in a card or on a page and the items its codes link to."""
    found: list[tuple[str, list[str]]] = await scope.locator(
        ".change-title, h1.review-title"
    ).evaluate_all(
        "els => els.map(e => [e.textContent.replace(/\\s+/g, ' ').trim(),"
        " [...e.querySelectorAll('a.item-link[data-ref]')].map(a => a.dataset.ref)])"
    )
    return found


async def no_hunk_marks_no_counted_head_and_codes_are_links() -> None:
    """No '@@' anywhere on the list, its rail, any review page or dialog (a hunk row and a fold
    row are plain words, also in a plan of 45 items whose folds are pressed open), no counted
    head line ('Plan 9: 7 to add', 'Proposal 7: 2 to move'), a plan's title is its own title,
    a move's title does not start with its code and the codes in it are links to the items."""
    from tests.acceptance_review import a_big_plan  # noqa: PLC0415

    _, project, _, made, seen = await a_busy_project()
    async with connect(project.mcp_url, project.key) as agent:
        epic = await make(agent, "epic", "Everything")
        big = await a_big_plan(agent, epic, "Everything for the launch")
        needs = await ok(agent, "needs_a_person")
        moves = {m["id"]: m for m in (await ok(agent, "list_placements", limit=50))["items"]}
    seen = Seen(needs, [], seen.later)
    wrong: list[str] = []
    hunks = 0
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        for mode in MODES:
            await the_list(w, seen, mode)
            await open_the_routine_group(page)
            text = await page.evaluate(PAGE_TEXT)
            if "@@" in text or COUNTED_HEAD.search(text):
                wrong.append(f"the list by {mode}")
        await the_list(w, seen)
        await open_the_routine_group(page)
        for row in seen.rows:
            change = change_id(row)
            title = await titles_of(card(page, change))
            if row["kind"] == "plan":
                (shown, _), *_ = title
                if shown != row["plan"]["title"]:
                    wrong.append(f"{change}: the card's title is {shown!r}")
            if row["kind"] == "placement":
                move = moves[row["placement"]["id"]]
                (shown, links), *_ = title
                if (
                    not shown.startswith("Move ")
                    or shown.startswith(("Proposal", "P-"))
                    or sorted(links)
                    != sorted(
                        [one["ref"] for one in move["items"]]
                        + ([move["parent"]] if move["parent"] else [])
                    )
                ):
                    wrong.append(f"{change}: the card's title {shown!r} links {links}")
        for row in seen.rows:
            change = change_id(row)
            await the_review_page(w, change)
            if change == f"plan:{big['plan']}":
                for _ in range(80):
                    folds = review(page).locator(".diff-fold")
                    if not await folds.count():
                        break
                    folded = spoken(await folds.first.inner_text())
                    if "@@" in folded or not re.search(r"\d", folded):
                        wrong.append(f"a fold row says {folded!r}")
                    await folds.first.click()
            hunks += await review(page).locator(".diff-hunk").count()
            text = await page.evaluate(PAGE_TEXT)
            if "@@" in text:
                wrong.append(f"{change}: @@ on the page")
            if COUNTED_HEAD.search(text):
                wrong.append(f"{change}: a counted head line")
            shown, links = (await titles_of(review(page)))[-1]
            if row["kind"] == "plan" and shown != row["plan"]["title"]:
                wrong.append(f"{change}: title {shown!r}")
            if row["kind"] == "placement":
                move = moves[row["placement"]["id"]]
                if not shown.startswith("Move ") or not {i["ref"] for i in move["items"]} <= set(
                    links
                ):
                    wrong.append(f"{change}: title {shown!r} links {links}")
        # The rail and every dialog of a plan.
        await the_review_page(w, f"plan:{made['plan']}")
        await review(page).locator("button.verdict[data-verb='reject']").click()
        await expect(page.locator("dialog[open]")).to_be_visible(timeout=SLOW)
        if "@@" in await page.evaluate(PAGE_TEXT):
            wrong.append("@@ in a dialog")
    proof(
        "M52",
        "no '@@' is on the list, its rail, any review page or a dialog (the hunk rows and the "
        "fold rows of a plan of 45 items, every fold pressed open, are plain words with a "
        "number), no counted head line ('Plan 9: 7 to add', 'Proposal 7: 2 to move') is on a "
        "card or a page, a plan's title is its own, a move's title starts with Move and not "
        "with its code, and every code in it (the stories it moves and where they go) is a "
        "link to that item",
        not wrong and hunks >= 3,
        f"{hunks} hunk rows read" + (f"; wrong: {wrong[:3]}" if wrong else ""),
    )


async def groups_wrong(w: Watched, seen: Seen) -> tuple[list[str], list[str]]:
    """Where each way of grouping puts each row, against what the server's fields say: the
    problems found and a line for each way."""
    page = w.page
    wrong: list[str] = []
    details: list[str] = []
    for mode in MODES:
        await the_list(w, seen, mode)
        asked = await page.locator("select#needs-group").input_value()
        address = await page.evaluate("location.hash")
        found = await page.locator(f"section.needs-group[data-by='{mode}']").evaluate_all(GROUPS)
        by_group = {g[0]: g for g in found}
        order = [g[0] for g in found if g[0] not in ("routine", "agent")]
        expected = seen.groups(mode)
        if asked != mode:
            wrong.append(f"{mode}: the choice reads {asked}")
        if ("group=" in address) != (mode != "level"):
            wrong.append(f"{mode}: the address says {address}")
        if order != seen.order(mode):
            wrong.append(f"{mode}: order {order} not {seen.order(mode)}")
        for key, ids in expected.items():
            page_ids = by_group.get(key, (None, "", "", []))[3]
            if sorted(page_ids) != sorted(ids):
                wrong.append(f"{mode}/{key}: {page_ids} not {ids}")
            if by_group.get(key, (None, "", "0", []))[2] != str(len(ids)):
                wrong.append(f"{mode}/{key}: count {by_group.get(key)}")
        counted = sum(int(g[2]) for g in found if g[0] != "agent")
        drawn = [i for g in found if g[0] != "agent" for i in g[3]]
        routine = by_group.get("routine", (None, "", "0", []))
        if counted != seen.total or len(drawn) != len(set(drawn)) or len(drawn) != seen.total:
            wrong.append(f"{mode}: the counts add up to {counted} of {seen.total}")
        if sorted(routine[3]) != sorted(seen.routine):
            wrong.append(f"{mode}: the routine group holds {routine[3]}")
        if mode == "decision":
            titles = {g[0]: g[1] for g in found}
            if not {"approve": "To approve", "answer": "To answer"}.items() <= titles.items():
                wrong.append(f"decision titles {titles}")
        if mode == "level":
            titles = {g[0]: g[1] for g in found}
            if (
                not {
                    "plan": "Plans",
                    "story": "Stories",
                    "task": "Tasks",
                    "note": "Notes",
                }.items()
                <= titles.items()
            ):
                wrong.append(f"level titles {titles}")
        if mode == "topic":
            titles = {g[0]: g[1] for g in found}
            labelled = {
                row["topic"]["id"]: row["topic"]["label"]
                for row in seen.rows
                if row["topic"] and change_id(row) not in seen.routine
            }
            if any(titles.get(k) != v for k, v in labelled.items()):
                wrong.append(f"topic titles {titles}")
        details.append(f"{mode} {len(expected)} groups")
    return wrong, details


async def the_groups_are_the_servers() -> None:
    """Each Group by choice puts every row where the server's level, decision or topic of that
    row says, with counts that add up, the routine moves and the waiting plans apart in every
    mode, and the choice in the address."""
    _, project, _, made, seen = await a_busy_project()
    async with browser_page(project.address, token=project.key, name=None) as w:
        wrong, details = await groups_wrong(w, seen)
        proof(
            "M52",
            "each Group by choice (Level, Topic and Decision) puts every row of what needs a "
            "person where the server's level, topic or decision of that row says, in the "
            "order the server's fields give (levels strategic to granular, decisions in order, "
            "topics by their longest wait), named as the server names them (a topic by its "
            "label), with counts that add up to the number of rows, no row lost or drawn twice, "
            "and the choice in the address (omitted for Level)",
            not wrong,
            ", ".join(details) + (f"; wrong: {wrong[:3]}" if wrong else ""),
        )


async def routine_and_waiting_groups_are_the_same_in_every_mode() -> None:
    """The routine moves stay in a closed group of their own and the plans that wait for their
    agent are a closed group after it, in every way of grouping."""
    _, project, _, made, seen = await a_busy_project()
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        wrong: list[str] = []
        for mode in MODES:
            await the_list(w, seen, mode)
            order = await page.locator("section.needs-group").evaluate_all(
                "els => els.map(e => e.dataset.group)"
            )
            tail = [g for g in order if g in ("routine", "agent")]
            expected_tail = (["routine"] if seen.routine else []) + ["agent"]
            if tail != expected_tail or order[-len(expected_tail) :] != expected_tail:
                wrong.append(f"{mode}: {order}")
            for name in ("routine", "agent"):
                group = page.locator(f"section.needs-group[data-group='{name}']")
                if not await group.count():
                    continue
                if await group.locator("details").evaluate("el => el.open"):
                    wrong.append(f"{mode}: {name} is open")
            waiting = page.locator("section.needs-group[data-group='agent'] li.waiting-row")
            numbers = await waiting.evaluate_all("els => els.map(e => Number(e.dataset.plan))")
            if numbers != seen.later:
                wrong.append(f"{mode}: waiting plans {numbers}")
            link = waiting.first.locator("a")
            href = await link.first.get_attribute("href")
            if f"review?change=plan:{seen.later[0]}" not in (href or ""):
                wrong.append(f"{mode}: link {href}")
            if await page.locator("section.needs-group[data-group='agent'] li.needs-row").count():
                wrong.append(f"{mode}: the waiting plan is drawn as a row")
        proof(
            "M52",
            "in every way of grouping the suggested moves the server calls routine stay in one "
            "closed group of their own and the plan that waits for its agent is a closed group "
            "after it (a line with its code and title as a link to its review page, in no row "
            "and in no count), both last on the page",
            not wrong,
            f"{len(seen.routine)} routine, {len(seen.later)} waiting"
            + (f"; {wrong[:2]}" if wrong else ""),
        )


# -- Open and the review page ----------------------------------------------------------------------


async def change_of(page: Page) -> str | None:
    """The change the address names, as the page reads it."""
    found: str | None = await page.evaluate(
        "new URLSearchParams(location.hash.split('?')[1] || '').get('change')"
    )
    return found


async def open_goes_to_the_review_page() -> None:
    """Open (and the title) of every kind of change goes to its own full-screen review page, never
    to the page of the item; Back returns to the list; the one way to the item's own page is the
    link on the review page."""
    _, project, _, made, seen = await a_busy_project()
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        wrong: list[str] = []
        opened: list[str] = []
        for row in seen.rows:
            change = change_id(row)
            await the_list(w, seen)
            if change in seen.routine:
                await open_the_routine_group(page)
            await card(page, change).locator("a.change-open").click()
            await expect(review(page)).to_have_attribute("data-change", change, timeout=SLOW)
            where = await page.evaluate("location.hash")
            if not where.startswith("#/review") or await change_of(page) != change:
                wrong.append(f"{change}: {where}")
            if await page.locator("#item-page").count():
                wrong.append(f"{change}: the item page")
            if await review(page).get_attribute("data-kind") is None:
                wrong.append(f"{change}: no kind")
            await page.locator("#review-back").click()
            await expect(page.locator("li.needs-row").first).to_be_visible(timeout=SLOW)
            if not (await page.evaluate("location.hash")).startswith("#/needs"):
                wrong.append(f"{change}: back went to {await page.evaluate('location.hash')}")
            opened.append(change)
        # The title opens it too, and the review page's own link is the one way to the item.
        await the_list(w, seen)
        ask = next(r for r in seen.rows if r["kind"] == "ask")
        await card(page, change_id(ask)).locator("a.change-title").click()
        await expect(review(page)).to_have_attribute("data-change", change_id(ask), timeout=SLOW)
        link = page.locator("a#review-item")
        to_item = await link.get_attribute("href")
        await link.click()
        await expect(page.locator("#item-page")).to_be_visible(timeout=SLOW)
        on_item = await change_of(page)
        proof(
            "M52",
            "Open on every kind of change (a plan, both suggested moves, a question, a check, "
            "notes, a refused approval and a claim that ran out) goes to that change's own "
            "review page (the address names the change, the page says what kind it is) and "
            "never to the page of the item, Back returns to the list, the title opens it too, "
            "and the item's own page is reached only from the review page's own link",
            not wrong
            and len(opened) == len(seen.rows)
            and ask["node"] in (to_item or "")
            and on_item is None,
            f"{len(opened)} changes" + (f"; wrong: {wrong[:2]}" if wrong else ""),
        )


async def text_of(page: Page, part: str) -> str:
    return spoken(await review(page).locator(f"section[data-part='{part}']").inner_text())


async def check_every_page(w: Watched, project: Any, made: dict[str, Any], seen: Seen) -> list[str]:
    """What the review page of each kind says, against what the tools say: the problems found."""
    by_kind = {r["kind"] if r["kind"] != "ask" else f"ask:{r['node']}": r for r in seen.rows}
    async with connect(project.mcp_url, project.key) as agent:
        plan = (await ok(agent, "list_plans", plan=made["plan"]))["items"][0]
        moves = {m["id"]: m for m in (await ok(agent, "list_placements", limit=50))["items"]}
        answered = {a["id"]: a for a in (await ok(agent, "list_asks", limit=50))["items"]}
        noted = await ok(agent, "list_notes", limit=50)
        task = await ok(agent, "get_node", node=made["checked"]["ref"])
        who = await ok(agent, "who_is_working")
    page = w.page
    wrong: list[str] = []

    async def read(change: str) -> dict[str, Any]:
        await the_review_page(w, change)
        return {
            "all": spoken(await review(page).inner_text()),
            "title": spoken(await review(page).locator("h1.review-title").inner_text()),
            "kind": await review(page).get_attribute("data-kind"),
            "parts": await review(page)
            .locator("section[data-part]")
            .evaluate_all("els => els.map(e => e.dataset.part)"),
        }

    # A plan: every item of the tree as an added line, the checks, the plan's title.
    page_of_plan = await read(f"plan:{made['plan']}")
    lines = (
        await review(page)
        .locator(f".change-diff {ITEM_LINES}")
        .evaluate_all("els => els.map(e => e.dataset.ref)")
    )
    if lines != [i["ref"] for i in plan["tree"]]:
        wrong.append(f"plan lines {lines}")
    if plan["title"] not in page_of_plan["title"] or "checks" not in page_of_plan["parts"]:
        wrong.append("plan head or checks")
    if "context" in page_of_plan["parts"]:
        wrong.append("a plan has no context part")

    # A suggested move: the moved items, the reason and the scores of the proposal.
    for name, number in (("stories", made["stories_moved"]), ("tasks", made["tasks_moved"])):
        move = moves[number]
        read_move = await read(f"placement:{number}")
        lines = (
            await review(page)
            .locator(".change-diff .diff-line[data-sign='+'][data-ref]")
            .evaluate_all("els => els.map(e => e.dataset.ref)")
        )
        scores = await review(page).locator(".diff-score[data-ref]").count()
        if sorted(lines) != sorted(i["ref"] for i in move["items"]):
            wrong.append(f"moves of {name}: lines {lines}")
        if move["reason"] not in read_move["all"]:
            wrong.append(f"moves of {name}: reason")
        if move["scores"] and scores != len(move["scores"]):
            wrong.append(f"moves of {name}: {scores} scores of {len(move['scores'])}")

    # A question: the question in full, the answers on offer, the effect and the work.
    for key in ("ask:" + made["asked"]["ref"], "ask:" + made["currency"]["ref"]):
        row = by_kind[key]
        ask = answered[row["ask"]["id"]]
        read_ask = await read(f"ask:{ask['id']}")
        for text in (ask["question"], *(ask["choices"] or [])):
            if text not in read_ask["all"]:
                wrong.append(f"{key}: {text}")
        if not await review(page).locator("[data-fact='effect']").count():
            wrong.append(f"{key}: no effect")
        context = await text_of(page, "context")
        if (
            row["title"] not in read_ask["title"]
            or row["node"] not in await review(page).inner_html()
        ):
            wrong.append(f"{key}: title")
        if not context:
            wrong.append(f"{key}: no context")

    # A yes or no question and a free one: the question in full and the kind of answer asked for.
    for number in (made["approval"], made["free"]):
        read_other = await read(f"ask:{number}")
        if answered[number]["question"] not in read_other["all"]:
            wrong.append(f"ask {number}: the question")

    async def missing(*texts: str) -> list[str]:
        """What the page does not say, after waiting for what it reads for itself (the notes, the
        checks and the claims of a change come a moment after the page)."""
        lost: list[str] = []
        for text in texts:
            try:
                await expect(review(page)).to_contain_text(text, timeout=SLOW)
            except AssertionError:
                lost.append(text)
        return lost

    # Notes: each note whole, with who decided it (or that nobody did) and who recorded it.
    row = by_kind["note"]
    await read(change_id(row))
    for note in [n for n in noted["items"] if row["node"] in n["nodes"]]:
        wrong += [
            f"note {note['id']}: {text}"
            for text in await missing(note["text"], note["why"], note["recorded_by"])
        ]

    # A check: the definition of done of the work, the open person-only check and the ticks.
    row = by_kind["check"]
    await read(change_id(row))
    wrong += [f"check {text}" for text in await missing(*[c["text"] for c in task["checks"]])]

    # A refused approval: what it waits for, who refused it, the answer and the comment.
    row = by_kind["gate"]
    await read(change_id(row))
    gate = row["gate"]
    gate_ask = answered[gate["ask"]]
    wrong += [
        f"gate {text}"
        for text in await missing(gate["question"], gate_ask["answer"], gate_ask["comment"])
    ]

    # A claim that ran out: who held it and the work.
    row = by_kind["lapse"]
    await read(change_id(row))
    lapsed = [
        a["actor"] for a in who["agents"] if any(h["node"] == row["node"] for h in a["lapsed"])
    ]
    if not lapsed:
        wrong.append("lapse: nobody held it")
    wrong += [f"lapse {text}" for text in await missing(*lapsed)]
    return wrong


async def every_kind_opens_a_page_that_says_what_the_tools_say() -> None:
    """The review page of each kind shows what the tools say about that change."""
    _, project, _, made, seen = await a_busy_project()
    async with browser_page(project.address, token=project.key, name=None) as w:
        wrong = await check_every_page(w, project, made, seen)
        proof(
            "M52",
            "the review page of every kind shows what the tools say of that change: a plan every "
            "item list_plans reads (as added lines, in order, with its checks), a suggested move "
            "the items, the reason and the scores list_placements gives, a question its text, "
            "its answers on offer, its effect and the work it is about, notes each in full with "
            "who recorded them, a check the work's definition of done, a refused approval what "
            "it asked and what was answered and said, and a claim that ran out who held it",
            not wrong,
            f"{len(seen.rows)} pages" + (f"; wrong: {wrong[:3]}" if wrong else ""),
        )


# -- the choices that act on one change -----------------------------------------------------

# What a change adds, removes or changes, as (sign, item, part) of every `+`, `-` and `~` line,
# sorted: the same change reads the same in the unified and the split view.
SIGNED = """el => [...el.querySelectorAll('.change-diff .diff-line')]
    .filter(l => ['+', '-', '~'].includes(l.dataset.sign))
    .map(l => `${l.dataset.sign}|${l.dataset.ref ?? ''}|${l.dataset.part ?? ''}`)
    .sort()"""


async def shape_of(page: Page) -> dict[str, Any]:
    """How the change on the review page is drawn: its mode, its layout, how many lines and sides,
    and what it adds, removes and changes."""
    box = review(page).locator(".change-diff")
    return {
        "mode": await box.get_attribute("data-mode"),
        "view": await box.get_attribute("data-view"),
        "lines": await box.locator(".diff-line").count(),
        "sides": await box.locator(".diff-side").count(),
        "signed": await review(page).evaluate(SIGNED),
    }


async def a_revised_plan(project: Any, owner: Any) -> tuple[int, dict[str, str]]:
    """A plan at revision 2: its agent changed a title and some details, took one task out and
    added one, after a person asked for changes."""
    from tests.acceptance_revising import an_epic, drafted, person  # noqa: PLC0415
    from tests.acceptance_revising_page import second_revision  # noqa: PLC0415

    async with connect(project.mcp_url, project.key) as agent:
        epic = await an_epic(agent)
        number, refs = await drafted(agent, epic)
        person(
            project,
            owner,
            "request_plan_changes",
            plan=number,
            revision=1,
            summary="Name the tyres and drop the covers task.",
            comments=[{"item": refs["tyres"], "field": "title", "text": "Say which tyres."}],
        )
        await ok(
            agent,
            "revise_plan",
            plan=number,
            items=second_revision(epic, refs),
            note="Named the routes and took out the covers task.",
        )
    return number, refs


async def the_toggles_change_the_lines() -> None:
    """Unified and Split, and Changes and Whole plan, each change the lines of the change on the
    review page, for a revised plan in both modes and for a suggested move, with the same set of
    added, removed and changed lines in both layouts; a question's page has no such choice, and
    the list has none."""
    owner = register()
    project = make_project(owner)
    number, _ = await a_revised_plan(project, owner)
    async with connect(project.mcp_url, project.key) as agent:
        from tests.test_placement_integration import a_sorted_project  # noqa: PLC0415

        refs = await a_sorted_project(agent)
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
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_review_page(w, f"plan:{number}")
        mode, view = page.locator("#review-mode"), page.locator("#review-view")
        await expect(review(page).locator(".change-diff[data-mode='delta']")).to_have_count(
            1, timeout=SLOW
        )

        def pressed(box: Locator, key: str) -> Locator:
            return box.locator(f"button[data-{key}]")

        wrong: list[str] = []
        shapes: dict[str, dict[str, Any]] = {}
        # Changes: the default of a revised plan.
        shapes["delta unified"] = await shape_of(page)
        await pressed(view, "view").filter(has_text="Split").click()
        await expect(review(page).locator(".change-diff[data-view='split']")).to_have_count(1)
        shapes["delta split"] = await shape_of(page)
        await pressed(view, "view").filter(has_text="Unified").click()
        await expect(review(page).locator(".change-diff[data-view='unified']")).to_have_count(1)
        # Whole plan: more lines than Changes, unified and split.
        await pressed(mode, "mode").filter(has_text="Whole").click()
        await expect(review(page).locator(".change-diff[data-mode='whole']")).to_have_count(1)
        shapes["whole unified"] = await shape_of(page)
        await pressed(view, "view").filter(has_text="Split").click()
        await expect(review(page).locator(".change-diff[data-view='split']")).to_have_count(1)
        shapes["whole split"] = await shape_of(page)
        address = await page.evaluate("location.hash")
        for name in ("delta", "whole"):
            one, two = shapes[f"{name} unified"], shapes[f"{name} split"]
            if (one["view"], two["view"]) != ("unified", "split"):
                wrong.append(f"{name}: views {one['view']}, {two['view']}")
            if one["sides"] != 0 or two["sides"] < 2 or two["lines"] < one["lines"]:
                wrong.append(f"{name}: sides {one['sides']} then {two['sides']}")
            if one["signed"] != two["signed"] or not one["signed"]:
                wrong.append(f"{name}: the lines differ between unified and split")
        if shapes["delta unified"]["lines"] == shapes["whole unified"]["lines"]:
            wrong.append("Changes and Whole plan have as many lines")
        if shapes["delta unified"]["signed"] == shapes["whole unified"]["signed"]:
            wrong.append("Changes and Whole plan show the same lines")
        # Whole plan again as Changes: the lines come back.
        await pressed(mode, "mode").filter(has_text="Changes").click()
        await expect(review(page).locator(".change-diff[data-mode='delta']")).to_have_count(1)
        back = await shape_of(page)
        if back["mode"] != "delta" or back["signed"] != shapes["delta split"]["signed"]:
            wrong.append("Changes did not come back")
        old_side = await review(page).locator(".diff-side[data-side='old']").count()
        new_side = await review(page).locator(".diff-side[data-side='new']").count()
        if (old_side, new_side) != (1, 1) or "view=split" not in address:
            wrong.append(f"sides {old_side}/{new_side}, address {address}")

        # A suggested move: Unified and Split, the same lines, before and after.
        await the_review_page(w, f"placement:{move}")
        await expect(review(page).locator("#review-mode")).to_have_count(0)
        before = await shape_of(page)
        await (
            review(page).locator("#review-view button[data-view]").filter(has_text="Split").click()
        )
        await expect(review(page).locator(".change-diff[data-view='split']")).to_have_count(1)
        after = await shape_of(page)
        if before["view"] != "unified" or after["view"] != "split" or before["sides"] != 0:
            wrong.append(f"move: {before['view']} {after['view']}")
        if after["sides"] < 2 or before["signed"] != after["signed"] or len(before["signed"]) < 4:
            wrong.append("move: the lines differ or are too few")

        # A change that is not a diff has no such choice, and nor has a plan at revision 1 a
        # choice of modes.
        async with connect(project.mcp_url, project.key) as agent:
            lone = await ok(agent, "create_node", kind="epic", title="Alone")
            story = await ok(agent, "create_node", kind="story", title="One", parent=lone["ref"])
            task = await ok(agent, "create_node", kind="task", title="Two", parent=story["ref"])
            await ok(agent, "claim_node", node=task["ref"])
            await ok(
                agent,
                "finish_work",
                node=task["ref"],
                outcome="blocked",
                note="Need a decision.",
                ask={"question": "Which one?", "choices": ["A", "B"]},
            )
            asked = (await ok(agent, "list_asks", status="open", limit=5))["items"][0]["id"]
            first = await ok(
                agent,
                "create_plan",
                draft=True,
                title="Fresh",
                items=[
                    {"key": "s", "kind": "story", "title": "Fresh story", "parent": lone["ref"]}
                ],
            )
        await the_review_page(w, f"ask:{asked}")
        no_choice = await review(page).locator("#review-mode, #review-view, .segmented").count()
        await the_review_page(w, f"plan:{first['plan']}")
        no_modes = await review(page).locator("#review-mode").count()
        has_layout = await review(page).locator("#review-view").count()
        if no_choice or no_modes or has_layout != 1:
            wrong.append(f"choices: question {no_choice}, fresh plan {no_modes}/{has_layout}")
        proof(
            "M52",
            "on the review page Unified and Split each change the lines of the change (a revised "
            "plan in Changes and in Whole plan, and a suggested move): Split draws the old and "
            "the new side, Unified none, and the added, removed and changed lines are exactly "
            "the same set in both; Changes and Whole plan show different lines and Changes "
            "comes back; the choice of layout is in the address; a question's "
            "page has no such choice and a plan at its first revision has only the layout",
            not wrong,
            f"delta {shapes['delta unified']['lines']} lines, "
            f"whole {shapes['whole unified']['lines']}"
            + (f"; wrong: {wrong[:3]}" if wrong else ""),
        )


async def previous_and_next_follow_the_list() -> None:
    """Previous and next on the review page follow the order the list showed, in every way of
    grouping, with the position."""
    _, project, _, made, seen = await a_busy_project()
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        wrong: list[str] = []
        for mode in MODES:
            await the_list(w, seen, mode)
            order = await page.locator("li.needs-row").evaluate_all(
                "els => els.map(e => e.dataset.change)"
            )
            await open_the_routine_group(page)
            first = card(page, order[0])
            await first.locator("a.change-open").click()
            walked: list[str] = []
            positions: list[str] = []
            for _ in range(len(order)):
                current = await change_of(page)
                walked.append(str(current))
                positions.append(spoken(await page.locator("#review-position").inner_text()))
                if len(walked) < len(order):
                    await page.locator("#review-next").click()
                    await expect(review(page)).not_to_have_attribute(
                        "data-change", str(current), timeout=SLOW
                    )
            last_next = await page.locator("#review-next.off[aria-disabled='true']").count() == 1
            if walked != order:
                wrong.append(f"{mode}: {walked} not {order}")
            if positions != [f"{n} of {len(order)}" for n in range(1, len(order) + 1)]:
                wrong.append(f"{mode}: positions {positions}")
            if not last_next:
                wrong.append(f"{mode}: next at the end")
            await page.locator("#review-prev").click()
            await expect(review(page)).to_have_attribute("data-change", order[-2], timeout=SLOW)
            where = await page.evaluate("location.hash")
            if ("group=" in where) != (mode != "level"):
                wrong.append(f"{mode}: the group is not kept ({where})")
        await the_review_page(
            w,
            f"ask:{seen.rows[0]['ask']['id']}"
            if seen.rows[0]["kind"] == "ask"
            else change_id(seen.rows[0]),
        )
        alone = await page.locator("#review-prev, #review-next, #review-position").count()
        proof(
            "M52",
            "previous and next on the review page walk the changes in exactly the order the "
            "list showed them in each way of grouping (Level, Topic and Decision), with the "
            "position ('3 of 9') and the choice of grouping kept in the address; next stops at "
            "the last one, previous goes back one, and a change opened by its address alone has "
            "no steps",
            not wrong and alone == 0,
            f"{len(seen.rows)} changes in 3 orders" + (f"; wrong: {wrong[:2]}" if wrong else ""),
        )


async def sizes_say_what_they_are() -> None:
    """On these pages a size reads Size S, with a hover card that says what it means; the Board's
    cards keep the bare letter."""
    _, project, _, made, seen = await a_busy_project()
    async with browser_page(project.address, token=project.key, name=None) as w:
        page = w.page
        await the_list(w, seen)
        check = card(page, f"check:{made['checked']['ref']}")
        chip = check.locator("[data-fact='size'] .chip")
        listed = spoken(await chip.first.inner_text())
        await chip.first.hover()
        hover = page.locator(".hovercard:visible")
        await expect(hover).to_be_visible(timeout=SLOW)
        said = spoken(await hover.first.inner_text())
        await the_review_page(w, f"plan:{made['plan']}")
        in_lines = (
            await review(page)
            .locator(".change-diff .chip.size, .change-diff .size-chip")
            .all_inner_texts()
        )
        written = spoken(await review(page).locator(".change-diff").inner_text())
        await w.open("#/board?show=both")
        cards = page.locator(f"article.card[data-ref='{made['checked']['ref']}']")
        await expect(cards).to_have_count(1, timeout=SLOW)
        on_the_board = spoken(await cards.inner_text())
        proof(
            "M52",
            "a size on the list and on the review page reads Size S (never the bare letter), "
            "its hover card says what it means ('Small, worth 2 points'), and the Board's "
            "cards keep the bare letter",
            listed == "Size S"
            and "Small" in said
            and "2 points" in said
            and "Size S" in written
            and all(text.startswith("Size") for text in in_lines)
            and "Size" not in on_the_board,
            f"{listed}: {said}",
        )


async def standard_on_the_page() -> None:
    await the_list_has_no_letters_counts_or_switches()
    await no_hunk_marks_no_counted_head_and_codes_are_links()
    await the_groups_are_the_servers()
    await routine_and_waiting_groups_are_the_same_in_every_mode()
    await open_goes_to_the_review_page()
    await every_kind_opens_a_page_that_says_what_the_tools_say()
    await the_toggles_change_the_lines()
    await previous_and_next_follow_the_list()
    await sizes_say_what_they_are()
    # What a decision records is in its own file, which builds on this one.
    from tests.acceptance_standard_decide import standard_decisions  # noqa: PLC0415

    await standard_decisions()
