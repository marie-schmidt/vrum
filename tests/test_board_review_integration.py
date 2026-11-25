"""M49 on the board: the page of what needs a person is a review of changes.

Every decision on the page is drawn as a change in the language of a diff: a plan is a tree of added
lines under a hunk header, a suggested move is the work removed from one parent and added under
another, a question is a thread on its item, and the buttons come after the diff with one plain
sentence about what they do. Driven in a real headless browser and compared with what a real MCP
client reads: a plan of sixty items reads whole (fifty references in the list, sixty in `tree`) and
each of its lines equals `get_node`, `list_links` and `list_rules`; the lines of a move equal
`list_placements`; a viewer sees the diff and no deciding button; Viewed folds a change, counts it,
survives the page being drawn again and takes itself off when the change changes; the keys move and
open and never decide; and at 390 pixels the rail is a drop-down and nothing is wider than the
window.
Every test makes an account and a project of its own."""

import math
import re
from typing import Any

import pytest
from mcp import Client
from playwright.async_api import Locator, Page, ViewportSize, expect

from tests.board_helpers import SLOW, browser_page, on_page, open_review, open_review_by_address
from tests.board_layout import SCREENS, Screen, blank_page, contrast, layout_context, settle
from tests.helpers import connect, ok, reindexed
from tests.test_board_needs_integration import (
    DECIDING,
    EMPTY,
    ask,
    no_overflow,
    rows_of,
    task_under,
)
from tests.world import join_project, make_project, register

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

NARROW: ViewportSize = {"width": 390, "height": 900}
# The text of a line is its sign for a screen reader first: "added: ".
SIGN_WORDS = re.compile(r"^(added|removed|existing): ")


def card_of(page: Page, change: str) -> Locator:
    return page.locator(f"#needs-body li.needs-row[data-change='{change}']")


def item_lines(card: Locator, sign: str = "+") -> Locator:
    """The lines of a plan or a move that are about an item itself (not its details or criteria)."""
    return card.locator(f".diff-line[data-sign='{sign}'][data-ref]:not([data-part])")


async def refs_of(lines: Locator) -> list[str]:
    found: list[str] = await lines.evaluate_all("els => els.map((e) => e.dataset.ref)")
    return found


async def words_of(line: Locator) -> str:
    """What a line says, without the "added: " a screen reader hears first."""
    return SIGN_WORDS.sub("", (await line.locator(".diff-code").text_content()) or "")


# -- the plans -------------------------------------------------------------------------------------

BODY_STORY = "\n".join(f"Detail line {n} of the story." for n in range(1, 10))
BODY_CRITERION = "\n".join(f"Check {n} of the criterion." for n in range(1, 9))


async def an_existing_epic(agent: Client) -> tuple[str, str]:
    """An epic with a story under it, for a plan to hang under and to point at."""
    epic = await ok(agent, "create_node", actor="x", kind="epic", title="Stations")
    story = await ok(
        agent, "create_node", actor="x", kind="story", title="Dock maintenance", parent=epic["ref"]
    )
    return str(epic["ref"]), str(story["ref"])


def plan_of_sixty(epic: str, story: str) -> dict[str, Any]:
    """One epic, four stories of ten tasks, and a story under the existing epic with fourteen: sixty
    items, of every size, priority and date, with links and acceptance criteria."""
    items: list[dict[str, Any]] = [
        {
            "key": "e",
            "kind": "epic",
            "title": "Winter fleet expansion",
            "body": "Get the fleet ready for the cold months.\nForty bikes, four stations.",
            "target_on": "2027-01-31",
        }
    ]
    sizes = ["s", "m", "l", "xl"]
    for number in range(1, 5):
        items.append(
            {
                "key": f"s{number}",
                "kind": "story",
                "title": f"Story {number} of the winter plan",
                "parent": "e",
                "size": sizes[number - 1],
                "priority": "high" if number == 1 else "normal",
                **({"start_on": "2026-12-01"} if number == 2 else {}),
                **({"target_on": "2026-12-20"} if number == 3 else {}),
                **({"body": BODY_STORY} if number == 1 else {}),
            }
        )
        for task in range(1, 11):
            items.append(
                {
                    "key": f"t{number}_{task}",
                    "kind": "task",
                    "title": f"Task {number}.{task} of the winter plan",
                    "parent": f"s{number}",
                    **({"size": "xs"} if task % 3 else {}),
                    **({"priority": "urgent"} if task == 4 and number == 3 else {}),
                }
            )
    items.append(
        {"key": "s0", "kind": "story", "title": "Dock heating", "parent": epic, "size": "m"}
    )
    items.extend(
        {"key": f"t0_{n}", "kind": "task", "title": f"Heating step {n}", "parent": "s0"}
        for n in range(1, 15)
    )
    assert len(items) == 60
    return {
        "items": items,
        "links": [
            {"source": "t1_2", "target": "t1_1", "link_type": "needs_first"},
            {"source": "t2_1", "target": "t1_10", "link_type": "needs_first"},
            {"source": "s0", "target": story, "link_type": "relates_to"},
        ],
        "rules": [
            {"title": "Every bike passes the grip test", "node": "s1", "body": BODY_CRITERION},
            {"title": "The covers fit", "node": "t3_4"},
        ],
    }


async def test_a_plan_of_sixty_items_reads_whole_and_each_line_equals_what_the_tools_say() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic, story = await an_existing_epic(agent)
        made = await ok(
            agent, "create_plan", draft=True, title="Winter fleet", **plan_of_sixty(epic, story)
        )
        number = made["plan"]
        # The server reads it whole: fifty references in the list, and sixty items in the tree.
        whole = (await ok(agent, "list_plans", plan=number))["items"][0]
        assert len(whole["items"]) == 50 and len(whole["tree"]) == 60
        tree = whole["tree"]
        assert tree[0]["kind"] == "epic" and whole["items"][0] == tree[0]["ref"]
        assert set(whole["items"]) <= {one["ref"] for one in tree}

        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(card_of(page, f"plan:{number}")).to_be_visible(timeout=SLOW)
        # The card is a preview: a few lines and how many more there are. The whole plan is on its
        # review page.
        preview = card_of(page, f"plan:{number}").locator(".change-preview")
        await expect(preview.locator(".diff-item-line")).to_have_count(6, timeout=SLOW)
        await expect(preview.locator("button.diff-fold")).to_have_text("54 more items")
        await open_review(page, f"plan:{number}")
        card = page.locator("#review-diff")
        await expect(item_lines(card)).to_have_count(60, timeout=SLOW)
        # The page's lines are the tree, in order, every one of them.
        assert await refs_of(item_lines(card)) == [one["ref"] for one in tree]
        await expect(card.locator(".diff-hunk").first).to_have_text(
            "1 epic, 5 stories, 54 tasks added"
        )
        # No counted head line, no squares and no @@ anywhere on the page.
        await expect(page.locator(".change-summary, .diffstat, .change-letter")).to_have_count(0)
        assert "@@" not in (await page.locator("#review-page").text_content() or "")

        # The existing items it hangs under and points at are context lines, as the server says.
        context = card.locator(".diff-line[data-sign='.'][data-context]")
        assert await refs_of(context) == [one["ref"] for one in whole["context"]]
        assert [one["ref"] for one in whole["context"]] == [epic, story]
        assert await context.evaluate_all("els => els.map((e) => e.dataset.context)") == [
            one["why"] for one in whole["context"]
        ]
        await expect(context.first).to_contain_text("Stations")

        # More than forty items: each story's tasks are folded behind a line that says how many, and
        # a press opens them. What is folded is in the page and hidden.
        folds = card.locator("button.diff-fold[data-fold^='tasks:']")
        assert [await one.text_content() for one in await folds.all()] == [
            *["10 more tasks"] * 4,
            "14 more tasks",
        ]
        assert len(await card.locator(".diff-item-line:visible").all()) == 6
        await folds.first.click()
        await expect(folds).to_have_count(4)
        assert len(await card.locator(".diff-item-line:visible").all()) == 16
        while await folds.count():
            await folds.first.click()
        await expect(card.locator(".diff-item-line:visible")).to_have_count(60)

        # Each item, as the page draws it, is what the tools say it is.
        drawn = await card.locator(".diff-item-line").evaluate_all(
            """(els) => els.map((e) => {
                const mine = (selector) => [...document.querySelectorAll(
                    `#review-diff ${selector}[data-ref="${e.dataset.ref}"]`
                )];
                return {
                    ref: e.dataset.ref, size: e.dataset.size ?? null,
                    priority: e.dataset.priority,
                    start: e.dataset.start ?? null, target: e.dataset.target ?? null,
                    title: e.querySelector('.diff-item').textContent.trim(),
                    chip: e.querySelector('.chip.size')?.dataset.size ?? null,
                    links: [...e.querySelectorAll('.diff-link')]
                        .map((a) => [a.dataset.linkType, a.dataset.target]),
                    criteria: mine('.diff-criterion').map((r) => Number(r.dataset.rule)),
                    bodies: mine(".diff-text[data-part='body']")
                        .map((b) => b.querySelector('.diff-code').textContent
                            .replace(/^added: /, '')),
                };
            })"""
        )
        assert [one["ref"] for one in drawn] == [one["ref"] for one in tree]
        for shown, served in zip(drawn, tree, strict=True):
            node = await ok(agent, "get_node", node=served["ref"])
            outgoing = [
                [link["link_type"], link["target"]]
                for link in (await ok(agent, "list_links", node=served["ref"]))["items"]
                if link["source"] == served["ref"]
            ]
            rules = (
                []
                if served["kind"] == "epic"
                else [r["id"] for r in (await ok(agent, "list_rules", node=served["ref"]))["items"]]
            )
            assert shown["title"].endswith(node["title"]) and node["title"] == served["title"]
            assert (shown["size"], shown["chip"]) == (node["size"], node["size"])
            assert shown["priority"] == node["priority"] == served["priority"]
            assert shown["start"] == node["start_on"] == served["start_on"]
            assert shown["target"] == node["target_on"] == served["target_on"]
            # What it needs first and its other links, as the server lists them; its acceptance
            # criteria by number.
            assert shown["links"] == outgoing
            assert shown["criteria"] == rules == [c["id"] for c in served["criteria"]]
            # The details are the written lines, raw.
            assert shown["bodies"] == (node["body"].split("\n") if node["body"] else [])
        # The priority arrow only when it is not Normal; a flag that says "no size" (M50) when none.
        high = card.locator(".diff-item-line[data-priority='high'] .chip.priority")
        await expect(high).to_have_count(1)
        await expect(
            card.locator(".diff-item-line[data-priority='normal'] .chip.priority")
        ).to_have_count(0)
        await expect(
            card.locator(".diff-item-line[data-ref] .diff-flag[data-check='unsized']").first
        ).to_be_visible()

        # The change reads top to bottom: the diff, then the bar. No sentence says what the buttons
        # do: each one's hover card does.
        order = await page.evaluate(
            """() => {
                const diff = document.querySelector('#review-diff');
                const bar = document.querySelector('#review-bar');
                const follows = Node.DOCUMENT_POSITION_FOLLOWING;
                return Boolean(diff.compareDocumentPosition(bar) & follows);
            }"""
        )
        assert order is True
        await expect(page.locator(".change-consequence")).to_have_count(0)
        bar = page.locator("#review-bar")
        await expect(bar.locator(DECIDING)).to_have_text(["Approve", "Request changes…", "Reject…"])
        await bar.locator("button.verdict[data-verb=approve]").focus()
        await expect(page.locator(".hovercard:visible")).to_have_text(
            "Frees all 60 items for agents to take. Nothing is dropped. It cannot be undone."
        )
        await bar.locator("button.verdict[data-verb=reject]").focus()
        await expect(page.locator(".hovercard:visible")).to_have_text(
            "Drops all 60 items and leaves your reason on each one. It cannot be undone."
        )
        assert w.problems == []


async def test_a_long_body_shows_six_lines_and_says_how_many_more_and_one_press_opens_them() -> (
    None
):
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic, story = await an_existing_epic(agent)
        made = await ok(
            agent,
            "create_plan",
            draft=True,
            title="Small plan",
            items=[
                {
                    "key": "s",
                    "kind": "story",
                    "title": "A story with a long body",
                    "parent": epic,
                    "body": BODY_STORY,
                },
                {"key": "t", "kind": "task", "title": "A task", "parent": "s", "size": "s"},
            ],
            rules=[{"title": "It is long", "node": "s", "body": BODY_CRITERION}],
        )
        page = w.page
        await open_review_by_address(w, f"plan:{made['plan']}")
        card = page.locator("#review-diff")
        # No hunk counts a kind that is not there; a plan under an existing epic has it as context.
        await expect(card.locator(".diff-hunk")).to_have_text("1 story, 1 task added")
        await expect(card.locator(".diff-line[data-context='parent']")).to_contain_text("Stations")
        body = card.locator(".diff-text[data-part='body']")
        await expect(body).to_have_count(9)
        await expect(card.locator(".diff-text[data-part='body']:visible")).to_have_count(6)
        fold = card.locator("button.diff-fold").first
        await expect(fold).to_have_text("Show 3 more lines")
        await expect(card.locator("button.diff-fold")).to_have_count(2)
        # The details are raw lines, exactly as written, and a criterion is its code and title.
        assert [await words_of(one) for one in await body.all()] == BODY_STORY.split("\n")
        criterion = card.locator(".diff-criterion")
        await expect(criterion).to_contain_text("It is long")
        await expect(criterion.locator(".item-code")).to_have_text(re.compile(r"^AC-\d+$"))
        await fold.click()
        await expect(card.locator(".diff-text[data-part='body']:visible")).to_have_count(9)
        await expect(card.locator("button.diff-fold")).to_have_count(1)
        await card.locator("button.diff-fold").click()
        await expect(card.locator("button.diff-fold")).to_have_count(0)
        await expect(card.locator(".diff-text[data-part='criteria']:visible")).to_have_count(8)
        # What was opened stays open when the page is drawn again.
        extra = await task_under(agent, story, "Something else")
        await ask(agent, extra, "blocked", question="Which one?", choices=["a", "b"])
        await expect(page.locator("#sidebar-needs-count")).to_have_text("2", timeout=SLOW)
        await expect(card.locator(".diff-text[data-part='body']:visible")).to_have_count(9)
        assert w.problems == []


# -- a suggested move ------------------------------------------------------------------------------


async def test_the_lines_of_a_suggested_move_are_the_old_places_minus_and_the_new_one_plus() -> (
    None
):
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        billing = await ok(agent, "create_node", actor="x", kind="epic", title="Billing")
        existing = [
            await ok(
                agent, "create_node", actor="x", kind="story", title=name, parent=billing["ref"]
            )
            for name in ("Receipts", "Invoices", "Reminders")
        ]
        misc = await ok(agent, "create_node", actor="x", kind="epic", title="Misc")
        inbox = await ok(agent, "create_node", actor="x", kind="epic", title="Inbox")
        loose = [
            await ok(agent, "create_node", actor="x", kind="story", title=name, parent=parent)
            for name, parent in (
                ("Refund screen", misc["ref"]),
                ("Refund email", inbox["ref"]),
                ("Refund limits", misc["ref"]),
            )
        ]
        moved = [loose[0]["ref"], loose[1]["ref"], loose[2]["ref"]]
        proposed = await ok(
            agent,
            "propose_placement",
            items=moved,
            parent=billing["ref"],
            reason="These all concern refunds on card payments.\nThey were filed loose.",
        )
        # The scores need the index: have it caught up before the server and the page are asked.
        await reindexed(agent)
        listed = (await ok(agent, "list_placements", status="proposed"))["items"][0]
        assert listed["id"] == proposed["id"]
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(card_of(page, f"placement:{listed['id']}")).to_be_visible(timeout=SLOW)
        await open_review(page, f"placement:{listed['id']}")
        card = page.locator("#review-page")
        await expect(card.locator(".change-letter, .diffstat, .change-summary")).to_have_count(0)
        await expect(card.locator(".change-kind .item-code")).to_have_text(
            f"Proposal {listed['id']}"
        )
        # The proposer's reason is the commit message, whole.
        assert await card.locator(".needs-move-reason").text_content() == listed["reason"]
        # The new parent: its first two children stay, for context, and the three come under it.
        hunks = card.locator(".diff-hunk")
        assert [(await one.text_content() or "").strip() for one in await hunks.all()] == [
            f"E-{billing['ref'].split(':')[1]} Billing",
            f"E-{misc['ref'].split(':')[1]} Misc",
            f"E-{inbox['ref'].split(':')[1]} Inbox",
        ]
        assert "@@" not in (await card.text_content() or "")
        assert await refs_of(card.locator(".diff-line[data-sign='.'][data-ref]")) == [
            existing[0]["ref"],
            existing[1]["ref"],
        ]
        added = item_lines(card, "+")
        removed = item_lines(card, "-")
        assert await refs_of(added) == [one["ref"] for one in listed["items"]] == moved
        # Removed from where each was: Misc has two of them, Inbox one.
        assert await refs_of(removed) == [moved[0], moved[2], moved[1]]
        for line, one in zip(await added.all(), listed["items"], strict=True):
            parent = one["from_parent"]
            await expect(line.locator(".diff-from")).to_contain_text(
                f"from E-{parent.split(':')[1]}"
            )
        # Each moved item says how alike it is to the parent, as the server scored it (M50).
        shown = await card.locator(".diff-score").evaluate_all(
            "els => els.map((e) => [e.dataset.ref, Number(e.dataset.percent)])"
        )
        # A whole percent, rounded down; a similarity below zero reads as 0%.
        assert shown == [
            [s["ref"], max(0, min(100, math.floor(s["similarity"] * 100)))]
            for s in listed["scores"]
            if s["similarity"] is not None
        ]
        await expect(card.locator(".change-consequence")).to_have_count(0)
        await page.locator("#review-bar button.verdict[data-verb=approve]").focus()
        await expect(page.locator(".hovercard:visible")).to_have_text(
            f"Moves 3 stories under E-{billing['ref'].split(':')[1]}. Nothing else changes. "
            "You can move them back by hand."
        )
        assert w.problems == []


# -- a viewer --------------------------------------------------------------------------------------


async def test_a_viewer_reads_the_whole_diff_and_the_answers_and_has_no_deciding_button() -> None:
    owner = register()
    project = make_project(owner)
    viewer = join_project(project, "viewer", "Vera Viewer")
    async with connect(project.mcp_url, project.key) as agent:
        epic, story = await an_existing_epic(agent)
        made = await ok(
            agent, "create_plan", draft=True, title="Winter fleet", **plan_of_sixty(epic, story)
        )
        task = await task_under(agent, story, "Choose a provider")
        await ask(agent, task, "blocked", question="Which provider?", choices=["Stripe", "Adyen"])
        async with browser_page(project.address, token=None, person=viewer, name=None) as w:
            page = w.page
            await w.open("#/needs")
            await on_page(page, "needs")
            plan = card_of(page, f"plan:{made['plan']}")
            await expect(plan.locator(".diff-item-line")).to_have_count(6, timeout=SLOW)
            question = card_of(page, f"ask:{(await ok(agent, 'list_asks'))['items'][0]['id']}")
            await expect(question.locator("ul.ask-offered li")).to_have_text(["Stripe", "Adyen"])
            await expect(page.locator(f"#needs-body li.needs-row {DECIDING}")).to_have_count(0)
            await expect(page.locator("#needs-body .decision-bar")).to_have_count(0)
            # Viewed is the person's own mark, so a viewer has it.
            await plan.locator("input.change-viewed").check()
            await expect(plan.locator(".change-preview")).to_be_hidden()
            await expect(page.locator("#needs-reviewed")).to_have_text("Reviewed 1 of 2")
            # The whole plan is on its review page, with no bar and no verb.
            await plan.locator("a.change-open").click()
            await expect(item_lines(page.locator("#review-diff"))).to_have_count(60, timeout=SLOW)
            await expect(page.locator("#review-bar, button.verdict")).to_have_count(0)
            assert w.problems == []


# -- viewed ----------------------------------------------------------------------------------------


async def a_plan_and_two_questions(
    agent: Client,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    epic, story = await an_existing_epic(agent)
    made = await ok(
        agent,
        "create_plan",
        draft=True,
        title="Winter plan",
        items=[
            {"key": "s", "kind": "story", "title": "Studded tyres", "parent": epic, "size": "m"},
            {"key": "t1", "kind": "task", "title": "Choose a supplier", "parent": "s"},
            {"key": "t2", "kind": "task", "title": "Fit them", "parent": "s", "body": BODY_STORY},
        ],
    )
    first = await task_under(agent, story, "Choose a provider")
    second = await task_under(agent, story, "Ship the checkout")
    await ask(agent, first, "blocked", question="Which provider?", choices=["Stripe", "Adyen"])
    await ask(agent, second, "released", question="Is it fine to ship?", approval=True)
    return made, first, second


async def test_viewed_folds_a_change_counts_it_and_survives_news_and_leaving_the_page() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        made, first, _ = await a_plan_and_two_questions(agent)
        # What a plan's duplicate check says depends on how much of the project is indexed, so what
        # was read is not what is read later while the index catches up: let it, before looking.
        await reindexed(agent)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        plan = card_of(page, f"plan:{made['plan']}")
        await expect(plan).to_be_visible(timeout=SLOW)
        reviewed = page.locator("#needs-reviewed")
        await expect(reviewed).to_have_text("Reviewed 0 of 3")
        await expect(page.locator("#needs-counts, .change-summary")).to_have_count(0)
        box = plan.locator("input.change-viewed")
        await expect(box).to_have_accessible_name(f"Viewed Plan {made['plan']}")
        await expect(box).not_to_be_checked()

        await box.check()
        await expect(plan).to_have_attribute("data-viewed", "true")
        await expect(plan).to_have_attribute("data-collapsed", "true")
        await expect(plan.locator(".change-preview")).to_be_hidden()
        # The bar stays: a change that was looked at is still decided.
        await expect(plan.locator(DECIDING)).to_have_text(
            ["Approve", "Request changes…", "Reject…"]
        )
        await expect(reviewed).to_have_text("Reviewed 1 of 3")
        # The rail ticks it, in the list and in the drop-down.
        link = page.locator(f"nav#needs-rail a.rail-change[data-change='plan:{made['plan']}']")
        await expect(link).to_have_attribute("data-viewed", "true")
        await expect(link.locator(".rail-viewed")).to_be_visible()
        assert "(viewed)" in (
            await page.locator(
                "select#needs-rail-select option", has_text="Winter plan"
            ).text_content()
            or ""
        )

        # News that draws the page again (an agent asks something else) changes none of it.
        third = await task_under(agent, first["parent"], "Wire the webhook")
        await ask(agent, third, "blocked", question="Which one?", choices=["a", "b"])
        await expect(page.locator("#needs-body li.needs-row")).to_have_count(4, timeout=SLOW)
        await expect(reviewed).to_have_text("Reviewed 1 of 4")
        await expect(plan).to_have_attribute("data-viewed", "true")
        await expect(plan.locator(".change-preview")).to_be_hidden()

        # Going to the review page and coming back keeps it (it lives until the board is reloaded).
        await (
            card_of(page, f"ask:{(await ok(agent, 'list_asks'))['items'][0]['id']}")
            .locator("a.change-open")
            .click()
        )
        await expect(page.locator("#review-page")).to_be_visible(timeout=SLOW)
        await page.locator("#review-back").click()
        await on_page(page, "needs")
        await expect(reviewed).to_have_text("Reviewed 1 of 4", timeout=SLOW)
        # Nothing but the theme is kept in the browser.
        assert set(await page.evaluate("Object.keys(localStorage)")) <= {"vrum.theme"}

        # Unmarking opens it again.
        await box.uncheck()
        await expect(plan.locator(".change-preview")).to_be_visible()
        await expect(reviewed).to_have_text("Reviewed 0 of 4")
        await box.check()

        # The change changes (an item of the plan is renamed): the mark comes off by itself, and
        # the change says so.
        item = (await ok(agent, "list_plans", plan=made["plan"]))["items"][0]["tree"][1]
        node = await ok(agent, "get_node", node=item["ref"])
        await ok(
            agent,
            "update_node",
            node=item["ref"],
            title="Choose a different supplier",
            expected_version=node["version"],
        )
        await expect(plan).to_have_attribute("data-viewed", "false", timeout=SLOW)
        await expect(plan.locator(".change-changed")).to_have_text("Changed since viewed")
        await expect(plan.locator(".change-preview")).to_be_visible()
        await expect(
            plan.locator(".diff-item-line", has_text="Choose a different supplier")
        ).to_have_count(1)
        await expect(reviewed).to_have_text("Reviewed 0 of 4")
        await expect(link).to_have_attribute("data-viewed", "false")
        # A change that did not change keeps its mark: the question is still the same.
        question = card_of(page, f"ask:{(await ok(agent, 'list_asks'))['items'][0]['id']}")
        await question.locator("input.change-viewed").check()
        await expect(reviewed).to_have_text("Reviewed 1 of 4")
        await ask_again(agent, first["parent"], "Another question?")
        await expect(page.locator("#needs-body li.needs-row")).to_have_count(5, timeout=SLOW)
        await expect(question).to_have_attribute("data-viewed", "true")
        assert w.problems == []


async def ask_again(agent: Client, story: str, words: str) -> None:
    extra = await task_under(agent, story, f"About {words}")
    await ask(agent, extra, "blocked", question=words, choices=["a", "b"])


async def test_a_comment_being_typed_survives_the_page_being_drawn_again() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        _, first, _ = await a_plan_and_two_questions(agent)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        target = card_of(page, f"ask:{(await ok(agent, 'list_asks'))['items'][0]['id']}")
        await target.locator("button.decision-comment").click()
        field = target.locator("div.decision-comment-box textarea")
        await field.fill("They cover the markets we sell in.")
        await ask_again(agent, first["parent"], "A new question?")
        await expect(page.locator("#needs-body li.needs-row")).to_have_count(4, timeout=SLOW)
        await expect(field).to_have_value("They cover the markets we sell in.")
        assert w.problems == []


# -- the keys --------------------------------------------------------------------------------------


async def the_card_in_focus(page: Page) -> str | None:
    found: str | None = await page.evaluate(
        "document.activeElement?.closest('li.needs-row')?.dataset.change ?? null"
    )
    return found


async def test_the_keys_move_and_open_and_never_decide_anything() -> None:
    owner = register(name="Priya Raman")
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        made, _, _ = await a_plan_and_two_questions(agent)
        asks = [
            item["ask"]["id"] for item in rows_of(await ok(agent, "needs_a_person")) if item["ask"]
        ]
        page = w.page
        plan = card_of(page, f"plan:{made['plan']}")
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(plan).to_be_visible(timeout=SLOW)
        # The whole plan is read before it can be marked viewed: a key waits for it as a box does.
        await expect(item_lines(plan)).to_have_count(3, timeout=SLOW)
        await expect(plan.locator("input.change-viewed")).to_be_enabled()
        order = [f"plan:{made['plan']}", *[f"ask:{n}" for n in asks]]
        assert (
            await page.locator("#needs-body li.needs-row").evaluate_all(
                "els => els.map((e) => e.dataset.change)"
            )
            == order
        )

        # j and k move between the headers of the changes, and stop at the ends.
        await page.keyboard.press("j")
        assert await the_card_in_focus(page) == order[0]
        await expect(plan.locator(".change-head")).to_be_focused()
        await page.keyboard.press("j")
        assert await the_card_in_focus(page) == order[1]
        await page.keyboard.press("k")
        await page.keyboard.press("k")
        assert await the_card_in_focus(page) == order[0]

        # v marks it viewed (which folds its preview) and takes the mark off.
        await page.keyboard.press("v")
        await expect(plan).to_have_attribute("data-viewed", "true")
        await expect(plan.locator(".change-preview")).to_be_hidden()
        await expect(page.locator("#needs-reviewed")).to_have_text("Reviewed 1 of 3")
        await page.keyboard.press("v")
        await expect(plan).to_have_attribute("data-viewed", "false")
        await expect(plan.locator(".change-preview")).to_be_visible()
        # o opens the change's review page, and u goes back to the list as it was.
        await page.keyboard.press("o")
        await expect(page.locator("#review-page")).to_be_visible(timeout=SLOW)
        assert f"change=plan:{made['plan']}" in await page.evaluate("location.hash")
        await page.keyboard.press("u")
        await on_page(page, "needs")
        await expect(plan).to_be_visible(timeout=SLOW)
        await plan.locator(".change-head").focus()

        # a and r only move the focus to the buttons of the decision. Nothing is sent.
        sent = [r for r in w.requests if r.endswith("/tool/approve_plan")]
        await page.keyboard.press("a")
        await expect(plan.locator("button.verdict[data-verb=approve]")).to_be_focused()
        await page.keyboard.press("r")
        await expect(plan.locator("button.verdict[data-verb=reject]")).to_be_focused()
        await expect(page.locator("dialog[open]")).to_have_count(0)
        # Enter on the header of the change, which is not a button, decides nothing either.
        await plan.locator(".change-head").focus()
        await page.keyboard.press("Enter")
        await page.keyboard.press("Space")
        await expect(page.locator("dialog[open]")).to_have_count(0)
        assert [r for r in w.requests if r.endswith("/tool/approve_plan")] == sent
        assert (await ok(agent, "list_plans", status="proposed"))["total"] == 1
        # On a question, a moves to the first answer and r to the last, and neither answers.
        await page.keyboard.press("j")
        question = card_of(page, order[1])
        await page.keyboard.press("a")
        await expect(question.locator("button.verdict").first).to_be_focused()
        await page.keyboard.press("r")
        await expect(question.locator("button.verdict").last).to_be_focused()
        assert (await ok(agent, "list_asks", status="answered"))["total"] == 0

        # A modifier leaves the key to the browser, and typing is the person's own.
        await plan.locator(".change-head").focus()
        await page.keyboard.press("Control+j")
        assert await the_card_in_focus(page) == order[0]
        await question.locator("button.decision-comment").click()
        field = question.locator("div.decision-comment-box textarea")
        await expect(field).to_be_focused()
        await page.keyboard.type("j k o v a r ?")
        await expect(field).to_have_value("j k o v a r ?")
        assert await the_card_in_focus(page) == order[1]
        await expect(page.locator(".hovercard:visible")).to_have_count(0)
        # And none of it while a dialog is open: the keys go to the dialog's own field.
        await plan.locator("button.verdict[data-verb=reject]").focus()
        await page.keyboard.press("Enter")
        dialog = page.locator("dialog#reject-dialog[open]")
        await expect(dialog).to_be_visible()
        await page.keyboard.type("jkv")
        await expect(dialog.locator("#reject-reason")).to_have_value("jkv")
        await dialog.locator("button", has_text="Cancel").click()
        await expect(page.locator("dialog#reject-dialog")).to_have_count(0)
        assert (await ok(agent, "list_plans", status="proposed"))["total"] == 1
        await expect(plan).to_be_visible()

        # ? shows the list in the Key button; it says that nothing decides.
        await page.evaluate("document.activeElement && document.activeElement.blur()")
        await page.keyboard.press("?")
        await expect(page.locator("#needs-keys-button")).to_be_focused()
        keys = page.locator(".hovercard:visible")
        await expect(keys).to_contain_text("Keys on this page")
        await expect(keys).to_contain_text("never approve, reject or answer")
        await page.keyboard.press("Escape")

        # Only a press of the button decides, and then the focus moves on to the next change.
        await plan.locator(".change-head").focus()
        await page.keyboard.press("a")
        await expect(plan.locator("button.verdict[data-verb=approve]")).to_be_focused()
        await page.keyboard.press("Enter")
        await expect(plan).to_have_count(0, timeout=SLOW)
        assert len([r for r in w.requests if r.endswith("/tool/approve_plan")]) == 1
        await expect(card_of(page, order[1]).locator(".change-head")).to_be_focused()
        assert (await ok(agent, "list_plans", status="approved"))["items"][0][
            "decided_by"
        ] == "Priya Raman"
        assert w.problems == []


# -- a narrow window and both themes ---------------------------------------------------------------


async def test_at_390_pixels_the_rail_is_a_drop_down_and_nothing_is_wider_than_the_window() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic, story = await an_existing_epic(agent)
        made = await ok(
            agent,
            "create_plan",
            draft=True,
            title="A plan whose title is long enough that it has to wrap on a narrow window " * 2,
            **plan_of_sixty(epic, story),
        )
        _ = await a_plan_and_two_questions(agent)
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        await expect(card_of(page, f"plan:{made['plan']}")).to_be_visible(timeout=SLOW)
        # A wide window has the rail, a list of every change by its story, and no drop-down.
        rail = page.locator("nav#needs-rail")
        await expect(rail).to_be_visible()
        await expect(page.locator("select#needs-rail-select")).to_be_hidden()
        links = rail.locator("a.rail-change")
        total = len(rows_of(await ok(agent, "needs_a_person")))
        await expect(links).to_have_count(total)
        await expect(rail.locator(".rail-title .heading-count")).to_have_text(str(total))
        await links.last.click()
        assert await the_card_in_focus(page) == await links.last.get_attribute("data-change")

        await page.set_viewport_size(NARROW)
        select = page.locator("select#needs-rail-select")
        await expect(select).to_be_visible()
        await expect(rail).to_be_hidden()
        await expect(select.locator("option").first).to_have_text(f"Changes ({total})")
        await no_overflow(page)
        # The whole plan on its review page, with its long titles and deep lines, is no wider
        # either.
        await open_review(page, f"plan:{made['plan']}")
        diff = page.locator("#review-diff")
        while await diff.locator("button.diff-fold").count():
            await diff.locator("button.diff-fold").first.click()
        await expect(diff.locator(".diff-item-line:visible")).to_have_count(60)
        await no_overflow(page)
        await page.locator("#review-back").click()
        await on_page(page, "needs")
        select = page.locator("select#needs-rail-select")
        # Choosing a change goes to it and puts the focus on its header.
        newest = (await ok(agent, "list_asks"))["items"][-1]["id"]
        await select.select_option(f"ask:{newest}")
        await expect(card_of(page, f"ask:{newest}").locator(".change-head")).to_be_focused()
        await expect(select).to_have_value("")
        assert w.problems == []


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("screen", [SCREENS[0], SCREENS[2]], ids=lambda screen: screen.name)
async def test_every_word_and_sign_of_a_review_reads_in_both_themes(
    screen: Screen, theme: str
) -> None:
    owner = register()
    project = make_project(owner)
    async with connect(project.mcp_url, project.key) as agent:
        epic, story = await an_existing_epic(agent)
        await ok(
            agent, "create_plan", draft=True, title="Winter plan", **plan_of_sixty(epic, story)
        )
        await a_plan_and_two_questions(agent)
        loose = await ok(agent, "create_node", actor="x", kind="epic", title="Misc")
        stray = await ok(
            agent, "create_node", actor="x", kind="story", title="Refunds", parent=loose["ref"]
        )
        await ok(
            agent, "propose_placement", items=[stray["ref"]], parent=epic, reason="It is billing."
        )
        async with layout_context(project.address, screen, theme) as (w, context):
            scratch = await blank_page(context)
            page = w.page
            await page.goto(f"{project.address}#/needs")
            await expect(page.locator("#needs-body li.needs-row").first).to_be_visible(timeout=SLOW)
            await settle(page)
            # Every kind of mark is on screen: added lines, a hunk row, the kind and the byline.
            for part in (
                ".diff-line[data-sign='+']",
                ".diff-hunk",
                ".change-kind",
                ".change-facts",
            ):
                await expect(page.locator(f"#needs-body {part}").first).to_be_visible()
            found = await contrast(w, scratch)
            assert found.checked >= 50, found.checked
            assert found.found == [], found.lines()
            assert w.problems == []


async def test_nothing_to_review_is_one_quiet_line_once_everything_is_decided() -> None:
    owner = register()
    project = make_project(owner)
    async with (
        connect(project.mcp_url, project.key) as agent,
        browser_page(project.address, token=project.key, name=None) as w,
    ):
        epic, _ = await an_existing_epic(agent)
        made = await ok(
            agent,
            "create_plan",
            draft=True,
            items=[{"key": "s", "kind": "story", "title": "One story", "parent": epic}],
        )
        page = w.page
        await w.open("#/needs")
        await on_page(page, "needs")
        card = card_of(page, f"plan:{made['plan']}")
        await card.locator("button.verdict[data-verb=approve]").click()
        await expect(page.locator("#needs-empty")).to_have_text(EMPTY, timeout=SLOW)
        # The focus is somewhere a keyboard can carry on from, not lost.
        await expect(page.locator("#needs-empty")).to_be_focused()
        await expect(page.locator("#needs-overview")).to_be_hidden()
        await expect(page.locator("#needs-rail")).to_be_hidden()
        assert (await ok(agent, "needs_a_person"))["total"] == 0
        assert w.problems == []
